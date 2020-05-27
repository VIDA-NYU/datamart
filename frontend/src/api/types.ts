export interface AugmentationTask {
  data: SearchResult;
}

export interface ColumnAggregations {
  [columnName: string]: string[];
}

export interface AugmentationInfo {
  type: string;
  left_columns: number[][];
  left_columns_names: string[][];
  right_columns: number[][];
  right_columns_names: string[][];
  agg_functions?: ColumnAggregations;
}

export interface SpatialCoverage {
  lat?: string;
  lon?: string;
  address?: string;
  point?: string;
  admin?: string;
  ranges: Array<{
    range: {
      coordinates: [[number, number], [number, number]];
      type: 'envelope';
    };
  }>;
}

export interface Metadata {
  id: string;
  filename?: string;
  name: string;
  description: string;
  size: number;
  nb_rows: number;
  columns: ColumnMetadata[];
  date: string;
  materialize: {};
  nb_profiled_rows: number;
  sample: string;
  source: string;
  version: string;
  spatial_coverage?: SpatialCoverage[];
}

export interface ColumnMetadata {
  name: string;
  structural_type: string;
  semantic_types: string[];
  num_distinct_values?: number;
  coverage?: Array<{}>;
  mean?: number;
  stddev?: number;
  plot?: PlotVega;
}

export interface PlotVega {
  type: string;
  data:
    | NumericalDataVegaFormat[]
    | TemporalDataVegaFormat[]
    | CategoricalDataVegaFormat[];
}

export interface SearchResult {
  id: string;
  score: number;
  // join_columns: Array<[string, string]>;
  metadata: Metadata;
  augmentation?: AugmentationInfo;
  supplied_id: string | null;
  supplied_resource_id: string | null;
  d3m_dataset_description: {};
  sample: string[][];
}

export interface SearchResponse {
  results: SearchResult[];
}

export interface Variable {
  type: string;
}

export interface TemporalVariable {
  type: 'temporal_variable';
  start?: string;
  end?: string;
}

export interface GeoSpatialVariable {
  type: 'geospatial_variable';
  latitude1: string;
  longitude1: string;
  latitude2: string;
  longitude2: string;
}

export type FilterVariables = TemporalVariable | GeoSpatialVariable;

export interface QuerySpec {
  keywords: string[];
  source?: string[];
  variables: FilterVariables[];
}

export interface RelatedToLocalFile {
  kind: 'localFile';
  file: File;
}

export interface RelatedToSearchResult {
  kind: 'searchResult';
  datasetId: string;
  datasetName: string;
  datasetSize: number;
}
export interface NumericalDataVegaFormat {
  count: number;
  bin_start: number;
  bin_end: number;
}

export interface TemporalDataVegaFormat {
  count: number;
  date_start: string;
  date_end: string;
}
export interface CategoricalDataVegaFormat {
  count: number;
  bin: string;
}

export type RelatedFile = RelatedToLocalFile | RelatedToSearchResult;
