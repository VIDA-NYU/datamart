import axios, { AxiosResponse, AxiosRequestConfig } from 'axios';
import {
  SearchResponse,
  SearchResult,
  FilterVariables,
  Metadata,
  ColumnMetadata,
  QuerySpec,
  RelatedFile,
  Session,
} from './types';
import { API_URL } from '../config';

const api = axios.create({
  baseURL: API_URL,
});

export const DEFAULT_SOURCES = [
  'data.baltimorecity.gov',
  'data.cityofchicago.org',
  'data.cityofnewyork.us',
  'data.ny.gov',
  'data.sfgov.org',
  'data.wa.gov',
  'finances.worldbank.org',
  'upload',
];

export enum RequestResult {
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR',
}

export enum RequestStatus {
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR',
  IN_PROGRESS = 'IN_PROGRESS',
}

export interface Response<T> {
  status: RequestResult;
  data?: T;
}

export interface SearchQuery {
  query?: string;
  filters?: FilterVariables[];
  sources?: string[];
  relatedFile?: RelatedFile;
}

export function search(q: SearchQuery): Promise<Response<SearchResponse>> {
  const spec: QuerySpec = {
    keywords: q.query,
    variables: q.filters ? [...q.filters] : [],
  };
  if (q.sources && q.sources.length > 0) {
    spec.source = q.sources;
  }

  const formData = new FormData();
  formData.append('query', JSON.stringify(spec));
  if (q.relatedFile) {
    if (q.relatedFile.kind === 'localFile') {
      formData.append('data_profile', q.relatedFile.token);
    } else if (q.relatedFile.kind === 'searchResult') {
      formData.append('data_id', q.relatedFile.datasetId);
    } else {
      throw new Error('Invalid RelatedFile argument');
    }
  }
  const config = {
    headers: {
      'content-type': 'multipart/form-data',
    },
  };

  return api
    .post('/search?_parse_sample=1', formData, config)
    .then((response: AxiosResponse) => {
      return {
        status: RequestResult.SUCCESS,
        data: response.data,
      };
    })
    .catch(error => {
      return {
        status: RequestResult.ERROR,
      };
    });
}

export function downloadToSession(datasetId: string, session: Session) {
  let url = `/download?session_id=${session.session_id}`;
  if (session.format) {
    url += `&format=${encodeURIComponent(session.format)}`;
  }
  if (session.format_options) {
    Object.entries(session.format_options).forEach(([key, value]) => {
      url += `&format_${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
    });
  }
  return api.post(url, { id: datasetId }).then(() => {});
}

export function augment(
  data: RelatedFile,
  task: SearchResult,
  session?: Session
): Promise<Response<Blob>> {
  const formData = new FormData();
  formData.append('task', JSON.stringify(task));
  if (data.kind === 'localFile') {
    formData.append('data', data.token);
  } else if (data.kind === 'searchResult') {
    formData.append('data_id', data.datasetId);
  } else {
    throw new Error('Invalid RelatedFile argument');
  }

  const config: AxiosRequestConfig = {
    responseType: 'blob',
    headers: {
      'content-type': 'multipart/form-data',
    },
  };
  let url = '/augment';
  if (session) {
    url += `?session_id=${session.session_id}`;
    if (session.format) {
      url += `&format=${encodeURIComponent(session.format)}`;
    }
    if (session.format_options) {
      Object.entries(session.format_options).forEach(([key, value]) => {
        url += `&format_${encodeURIComponent(key)}=${encodeURIComponent(
          value
        )}`;
      });
    }
  }
  return api
    .post(url, formData, config)
    .then((response: AxiosResponse) => {
      if (response.status !== 200) {
        throw new Error('Status ' + response.status);
      }
      return {
        status: RequestResult.SUCCESS,
        data: response.data,
      };
    })
    .catch(error => {
      return {
        status: RequestResult.ERROR,
      };
    });
}

export interface CustomFields {
  [id: string]: {
    label: string;
    required: boolean;
    type: string;
  };
}

export interface UploadData {
  name: string;
  description?: string;
  address?: string;
  file?: File;
  manualAnnotations?: { columns: ColumnMetadata[] };
  customFields: Map<string, string>;
}

export function upload(data: UploadData) {
  const formData = new FormData();
  formData.append('name', data.name);

  if (data.description) {
    formData.append('description', data.description);
  }

  if (data.address) {
    formData.append('address', data.address);
  } else if (data.file) {
    formData.append('file', data.file);
  }
  if (data.manualAnnotations) {
    formData.append(
      'manual_annotations',
      JSON.stringify(data.manualAnnotations)
    );
  }

  // Custom fields
  data.customFields.forEach((value, field) => {
    formData.append(field, value);
  });

  const config: AxiosRequestConfig = {
    maxRedirects: 0,
    headers: {
      'content-type': 'multipart/form-data',
    },
  };

  return api.post('/upload', formData, config);
}

export interface ProfileResult extends Metadata {
  token: string;
}

export async function profile(file: File | string): Promise<ProfileResult> {
  const formData = new FormData();
  formData.append('data', file);
  const config = {
    headers: {
      'content-type': 'multipart/form-data',
    },
  };
  const response = await api.post('/profile', formData, config);
  return response.data;
}

export async function metadata(datasetId: string): Promise<Metadata> {
  const response = await api.get('/metadata/' + datasetId);
  return response.data.metadata;
}

export interface RecentDiscovery {
  id: string;
  discoverer: string;
  discovered: Date;
  profiled: Date;
  name: string;
  spatial?: boolean;
  temporal?: boolean;
}

export interface Status {
  recent_discoveries: RecentDiscovery[];
  sources_counts: {
    [source: string]: number;
  };
  custom_fields?: CustomFields;
}

export async function status(): Promise<Status> {
  const response = await api.get('/statistics');
  return response.data;
}

let statusPromise: Promise<Status> | undefined = undefined;

export function sources(): Promise<string[]> {
  if (!statusPromise) {
    statusPromise = status();
  }
  return statusPromise.then(response => Object.keys(response.sources_counts));
}

export function customFields(): Promise<CustomFields> {
  if (!statusPromise) {
    statusPromise = status();
  }
  return statusPromise.then(response => response.custom_fields || {});
}
