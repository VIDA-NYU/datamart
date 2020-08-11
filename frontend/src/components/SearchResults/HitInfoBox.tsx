import React from 'react';
import { SearchResult, InfoBoxType, Session } from '../../api/types';
import { formatSize } from '../../utils';
import {
  Description,
  DatasetTypes,
  DownloadButtons,
  DatasetColumns,
  SpatialCoverage,
} from './Metadata';
import { DatasetSample } from './DatasetSample';
import { AugmentationOptions } from './AugmentationOptions';
import { SearchQuery } from '../../api/rest';

function HitInfoBox(props: {
  hit: SearchResult;
  searchQuery: SearchQuery;
  infoBoxType: InfoBoxType;
  session?: Session;
}) {
  const { hit, searchQuery, infoBoxType, session } = props;
  return (
    <div
      className="col-md-8 px-0 pb-5 card shadow-sm ml-2"
      style={{
        maxHeight: '90vh',
        overflowY: 'scroll',
      }}
    >
      <div className="card-body d-flex flex-column">
        <h4>{hit.metadata.name}</h4>
        {infoBoxType === InfoBoxType.AUGMENTATION ? (
          <AugmentationOptions
            hit={hit}
            session={session}
            searchQuery={searchQuery}
          />
        ) : (
          <>
            <div className="mt-2">
              <b>ID:</b> {hit.id}
            </div>
            <div className="mt-2">
              <b>Source:</b> {hit.metadata.source}
            </div>
            <Description hit={hit} label={true} />
            {'types' in hit.metadata && <DatasetTypes hit={hit} label={true} />}
            <DatasetColumns
              columns={hit.metadata.columns}
              maxLength={200}
              label={true}
            />
            <div className="mt-2">
              <b>Size:</b> {formatSize(hit.metadata.size)}
            </div>
            <div className="mt-2">
              <DownloadButtons hit={hit} session={session} />
            </div>
            <div className="mt-2">
              <SpatialCoverage hit={hit} />
            </div>
            <div className="mt-2">
              <DatasetSample hit={hit} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export { HitInfoBox };
