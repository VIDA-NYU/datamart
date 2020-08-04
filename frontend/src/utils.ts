import { ColumnMetadata, ColumnType } from './api/types';

export function generateRandomId() {
  return Math.random()
    .toString(36)
    .substr(2, 9);
}

export function formatSize(size: number) {
  const units = ['bytes', 'kb', 'mb', 'gb', 'tb'];
  for (let i = 0; i < units.length; i++) {
    const unitMultiplier = Math.pow(1024, i);
    const unitMaximum = unitMultiplier * 1024;
    if (size < unitMaximum) {
      return `${(size / unitMultiplier).toFixed(1)} ${units[i]}`;
    }
  }
  return size;
}

export function triggerFileDownload(file: Blob, filename: string) {
  const link = document.createElement('a');
  link.href = window.URL.createObjectURL(file);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

export function cloneObject<T>(object: T): T {
  return JSON.parse(JSON.stringify(object));
}

export function shallowEqual(a: object, b: object) {
  if (a === b) {
    return true;
  }

  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (bKeys.length !== aKeys.length) {
    return false;
  }

  const ao = a as { [key: string]: unknown };
  const bo = b as { [key: string]: unknown };
  for (let i = 0; i < aKeys.length; i++) {
    const key = aKeys[i];
    if (ao[key] !== bo[key]) {
      return false;
    }
  }
  return true;
}

// The search would check if a part of the string to match with the provided substring. Then,
// it will return the index of the first result element from the array.
// If there is no element found then it will return -1
export function getIndexMatch(array: string[], substring: string) {
  const index = array.findIndex(element => element.includes(substring));
  return index;
}

// Check if a substring exists in some of the array values.
export function isSubstrInclude(array: string[], substring: string) {
  const index = getIndexMatch(array, substring);
  return index !== -1;
}

export function updateLatLonDropdown(
  array1: string[],
  array2: string[],
  column: ColumnMetadata,
  isLat: boolean
) {
  const dropdrownOptionsLatLon: string[] = [];
  const columnType = isLat ? ColumnType.LATITUDE : ColumnType.LONGITUDE;
  const columnPair = isLat ? ColumnType.LONGITUDE : ColumnType.LATITUDE;
  const valuePair = column.latlong_pair ? column.latlong_pair : ' ';
  const initialPairLabel = '-(pair';
  const endPairLabel = ')';

  if (array1.length > 0) {
    if (
      isSubstrInclude(column['semantic_types'], columnType) &&
      !array2.includes(valuePair)
    ) {
      dropdrownOptionsLatLon.push(
        columnPair + initialPairLabel + valuePair + endPairLabel
      );
    } else {
      for (let i = 0; i < array1.length; i++) {
        const pair = array1[i].toString();
        if (!array2.includes(pair) && !array2.includes(valuePair)) {
          dropdrownOptionsLatLon.push(
            columnPair + initialPairLabel + pair + endPairLabel
          );
        }
      }
    }

    let newPair = 0;
    do {
      newPair++;
    } while (
      array1.includes(newPair.toString()) ||
      array2.includes(newPair.toString())
    );
    const newPairName = initialPairLabel + newPair.toString() + endPairLabel;
    if (
      !isSubstrInclude(column['semantic_types'], columnType) &&
      !array2.includes(valuePair) &&
      !array2.includes(newPair.toString())
    ) {
      dropdrownOptionsLatLon.push(ColumnType.LATITUDE + newPairName);
      dropdrownOptionsLatLon.push(ColumnType.LONGITUDE + newPairName);
    }
  }
  return dropdrownOptionsLatLon;
}
