/**
  Веб-приложение для приёма файлов через GET/POST и сохранения на Google Диск.

  Адрес деплоя и ключ доступа хранятся в KeePass-базе (запись
  google_script_key): url — адрес веб-приложения, password — ключ, зеркало
  которого лежит в ALLOWED_KEYS ниже. Запрос без pass-параметра,
  совпадающего с ALLOWED_KEYS, отклоняется. Примеры вызовов
  (GOOGLE_DEPLOYMENT_ID и GOOGLE_SCRIPT_KEY подставьте из записи
  google_script_key в KeePass):

  curl -L "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=test.txt&data=Hello%20World&pass=$GOOGLE_SCRIPT_KEY"

  curl -v -L -X POST -d "data=Hello World&pass=$GOOGLE_SCRIPT_KEY" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=hello_world.md"

  curl -v -L -X POST --data-urlencode "data@file.txt" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=test.txt&pass=$GOOGLE_SCRIPT_KEY"

  curl -v -L "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=some.data.txt&data=some-data&pass=$GOOGLE_SCRIPT_KEY"

 */

// Зеркало ключа из записи google_script_key KeePass-базы; при деплое
// замените тестовое значение на реальный ключ из базы.
const ALLOWED_KEYS = [
  'test-google-drive-script-key',
];

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
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

    let folder;
    const folders = DriveApp.getFoldersByName('pyntara');
    if (folders.hasNext()) {
      folder = folders.next();
    } else {
      folder = DriveApp.createFolder('pyntara');
    }
    if (!folder) {
      throw new Error("Could not get or create 'pyntara' folder");
    }

    const blob = Utilities.newBlob(data, 'application/octet-stream', finalName);
    const file = folder.createFile(blob);
    if (!file) {
      throw new Error("Failed to create file");
    }

    return ContentService.createTextOutput('OK ' + finalName);
  } catch (error) {
    return ContentService.createTextOutput('ERROR: ' + error.message);
  }
}
