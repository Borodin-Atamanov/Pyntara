/**
  Web app that receives files via GET/POST and saves them to Google Drive.

  Protocol: the file content is sent in the data parameter in Base64
  encoding. Base64 is mandatory for binary data (encrypted System Metrics
  PDFs): Apps Script decodes the request body as UTF-8, which corrupts
  arbitrary bytes, while Base64 consists only of ASCII and survives
  unchanged. The file name is the filename parameter, the access key is the
  pass parameter. The response is the text OK <name> or ERROR: <reason>.

  In the repository the script is stored as a template: the real access key
  lives only in the KeePass database (the google_script_key entry, the
  password field), and the deploy script deploy_google_script.sh
  substitutes it into ALLOWED_KEYS in place of the __GOOGLE_SCRIPT_KEY__
  placeholder during the build. Deploying the file as is leaves a
  non-working placeholder: the script will not start.

  The deployment URL and the key are stored in the KeePass database (the
  google_script_key entry): url is the web app URL, password is the key.
  Call examples (substitute GOOGLE_DEPLOYMENT_ID and GOOGLE_SCRIPT_KEY from
  the entry; DATA_BASE64 is the Base64 of the file content):

  # GET, only for small files: URL length is limited
  curl -L "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec?filename=hello.txt&data=SGVsbG8gV29ybGQ%3D&pass=$GOOGLE_SCRIPT_KEY"

  # POST, data in the form body; --data-urlencode is required, otherwise
  # + / = characters in Base64 are read as form separators
  curl -L -X POST \
    --data-urlencode "filename=report.pdf" \
    --data-urlencode "pass=$GOOGLE_SCRIPT_KEY" \
    --data-urlencode "data=SGVsbG8gV29ybGQ=" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec"

  # POST from a Base64 file: first run base64 -w0 report.pdf > report.b64
  curl -L -X POST \
    --data-urlencode "filename=report.pdf" \
    --data-urlencode "pass=$GOOGLE_SCRIPT_KEY" \
    --data-urlencode "data@report.b64" \
    "https://script.google.com/macros/s/$GOOGLE_DEPLOYMENT_ID/exec"

 */

// Access key placeholder: deploy_google_script.sh substitutes the password
// value of the google_script_key KeePass entry here through json.dumps, so
// quotes and special characters in the key are safe. The file does not
// start without the substitution: the placeholder is undefined.
const ALLOWED_KEYS = [
  __GOOGLE_SCRIPT_KEY__,
];

// Extension -> MIME type of the saved file; without a match
// application/octet-stream is used.
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
