/**
 * This function loads variables from <meta> HTML tags. It allows us to
 * dynamically configure the base path for the application. It can useful
 * when configuring the system to run behind a proxy under a non-root path
 * such as: http://example.com/auctus/{all-paths}.
 *
 * This can be configured by adding an HTML meta-tag to the static HTML.
 *     <meta name="base_url" content="https://auctus.vida-nyu.org/">
 *     <meta name="api_url" content="https://auctus.vida-nyu.org/api/v1/">
 */
function loadVariableFromHTML(name: string): string {
  const meta = document.getElementsByName(name)[0];
  let value: string | null = meta ? meta.getAttribute('content') : null;
  if (!value) {
    value = '';
  } else if (value.endsWith('/')) {
    value = value.substring(0, value.length - 1);
  }
  return value;
}

/*
 * During web development,
 * - the web server is started via "npm start" and it runs at localhost:3000;
 * - the API server should be running at the address configured in the "proxy"
 *   key from the package.json file in the project's root directory
 *
 * In the client code, we always send the API requests to address where the
 * page is being served from. In 'development' mode, create-react-app dev
 * server will proxy the requests to the appropriate backend running the REST
 * API. In production, the app is already served from the same server where
 * the REST API is server, so the requests will work seamlessly.
 */

let baseUrl = `//${window.location.host}`;
let apiUrl = baseUrl;
let nominatimUrl = undefined;

const isDev = process.env.NODE_ENV === 'development';
if (isDev && process.env.REACT_APP_BASE_URL) {
  baseUrl = process.env.REACT_APP_BASE_URL;
}
if (isDev && process.env.REACT_APP_API_URL) {
  apiUrl = process.env.REACT_APP_API_URL;
}
if (isDev && process.env.REACT_APP_NOMINATIM_URL) {
  nominatimUrl = process.env.REACT_APP_NOMINATIM_URL;
}

const BASE_URL: string = loadVariableFromHTML('base_url') || baseUrl;
const API_URL = loadVariableFromHTML('api_url') || apiUrl;
const NOMINATIM_URL = loadVariableFromHTML('nominatim_url') || nominatimUrl;

console.log('BASE_URL', BASE_URL);
console.log('API_URL', API_URL);
console.log('NOMINATIM_URL', NOMINATIM_URL);

export { BASE_URL, API_URL, NOMINATIM_URL };
