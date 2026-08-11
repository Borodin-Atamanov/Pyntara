/**
  Веб-приложение для приёма файлов через GET/POST и сохранения на Google Диск.

  Примеры вызовов:

   GOOGLE_DEPLOYMENT_ID=AKfycbwS8UUcp_qu4Q9i9D0qklDItSMQMie7LjROprgMAEX_TYu5MRUx3XBX28qV_sTw4nyL

  curl -L "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=test.txt&data=Hello%20World"

  curl -v -L -X POST -d "data=Hello World" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=hello_world.md"

  curl -v -L -X POST --data-urlencode "data@file.txt" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=test.txt"

  curl -v -L "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=some.data.txt&data=some-data"

 */

// Авторизованные ключи доступа; запрос без ключа из этого списка отклоняется.
const ALLOWED_KEYS = [
  'CHANGE_ME_first_key',
  'CHANGE_ME_second_key',
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

    const key = e.parameter.key;
    if (!key || !ALLOWED_KEYS.includes(key)) {
      throw new Error("Unauthorized: missing or invalid key");
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
