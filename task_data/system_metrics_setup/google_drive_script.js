/**
  Веб-приложение для приёма файлов через GET/POST и сохранения на Google Диск.

  Протокол: содержимое файла передаётся в параметре data в кодировке Base64.
  Base64 обязателен для бинарных данных (зашифрованные PDF System Metrics):
  Apps Script декодирует тело запроса как UTF-8, что портит произвольные
  байты, а Base64 состоит только из ASCII и проходит без потерь. Имя файла —
  параметр filename, ключ доступа — параметр pass. Ответ — текст OK <имя>
  или ERROR: <причина>.

  В репозитории скрипт хранится как шаблон: реальный ключ доступа живёт
  только в KeePass-базе (запись google_script_key, поле password), и скрипт
  деплоя deploy_google_script.sh подставляет его в ALLOWED_KEYS на место
  плейсхолдера __GOOGLE_SCRIPT_KEY__ при сборке. Деплой файла как есть
  оставит нерабочий плейсхолдер — скрипт не запустится.

  Адрес деплоя и ключ хранятся в KeePass-базе (запись google_script_key): url —
  адрес веб-приложения, password — ключ. Примеры вызовов (GOOGLE_DEPLOYMENT_ID
  и GOOGLE_SCRIPT_KEY подставьте из записи; DATA_BASE64 — Base64 содержимого
  файла):

  # GET, только для небольших файлов: длина URL ограничена
  curl -L "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=hello.txt&data=SGVsbG8gV29ybGQ%3D&pass=$GOOGLE_SCRIPT_KEY"

  # POST, данные в теле формы; --data-urlencode обязателен, иначе символы
  # + / = в Base64 будут прочитаны как разделители формы
  curl -L -X POST \
    --data-urlencode "filename=report.pdf" \
    --data-urlencode "pass=$GOOGLE_SCRIPT_KEY" \
    --data-urlencode "data=SGVsbG8gV29ybGQ=" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec"

  # POST из файла с Base64: сначала base64 -w0 report.pdf > report.b64
  curl -L -X POST \
    --data-urlencode "filename=report.pdf" \
    --data-urlencode "pass=$GOOGLE_SCRIPT_KEY" \
    --data-urlencode "data@report.b64" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec"

 */

// Плейсхолдер ключа доступа: deploy_google_script.sh подставляет сюда
// значение password из записи google_script_key KeePass-базы через
// json.dumps, поэтому кавычки и спецсимволы ключа безопасны. Файл без
// подстановки не запускается: плейсхолдер не определён.
const ALLOWED_KEYS = [
  __GOOGLE_SCRIPT_KEY__,
];

// Расширение -> MIME-тип сохраняемого файла; без совпадения используется
// application/octet-stream.
const MIME_BY_EXTENSION = {
  'pdf': 'application/pdf',
  'txt': 'text/plain',
  'log': 'text/plain',
  'json': 'application/json',
};

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function mimeTypeForName(name) {
  const dot = name.lastIndexOf('.');
  if (dot < 0) {
    return 'application/octet-stream';
  }
  const ext = name.slice(dot + 1).toLowerCase();
  return MIME_BY_EXTENSION[ext] || 'application/octet-stream';
}

function handleRequest(e) {
  try {
    if (!e || !e.parameter) {
      throw new Error("No request parameters");
    }

    const pass = e.parameter.pass;
    if (!pass || !ALLOWED_KEYS.includes(pass)) {
      throw new Error("Unauthorized: missing or invalid pass");
    }

    const data = e.parameter.data;
    if (!data) {
      throw new Error("No 'data' parameter");
    }

    const rawFilename = e.parameter.filename || 'x3.dat';
    const safeFilename = rawFilename.replace(/[\/\\]/g, '_');

    const timestamp = Utilities.formatDate(new Date(), 'GMT-3', 'yyyy-MM-dd-HH-mm-ss');
    const random = String(Math.floor(Math.random() * 1000)).padStart(3, '0');
    const finalName = timestamp + '-' + random + '-' + safeFilename;

    var folder;
    const folders = DriveApp.getFoldersByName('pyntara');
    if (folders.hasNext()) {
      folder = folders.next();
    } else {
      folder = DriveApp.createFolder('pyntara');
    }
    if (!folder) {
      throw new Error("Could not get or create 'pyntara' folder");
    }

    const bytes = Utilities.base64Decode(data);
    const blob = Utilities.newBlob(bytes, mimeTypeForName(safeFilename), finalName);
    const file = folder.createFile(blob);
    if (!file) {
      throw new Error("Failed to create file");
    }

    return ContentService.createTextOutput('OK ' + finalName);
  } catch (error) {
    return ContentService.createTextOutput('ERROR: ' + error.message);
  }
}
