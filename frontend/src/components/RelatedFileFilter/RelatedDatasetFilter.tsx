import React from 'react';
import { CardShadow } from '../visus/Card/Card';
import { formatSize } from '../../utils';
import { PersistentComponent } from '../visus/PersistentComponent/PersistentComponent';
import { SearchResult } from '../../api/types';

interface RelatedDatasetFilterProps {
  datasetResult?: SearchResult;
}

class RelatedDatasetFilter extends PersistentComponent<RelatedDatasetFilterProps> {

  render() {
    const datasetResult = this.props.datasetResult;
    if (datasetResult) {
      return (
        <div>
          <CardShadow height={'auto'}>
            <span className="font-weight-bold">Selected file:</span> {datasetResult.metadata.name}{' '}
            ({formatSize(datasetResult.metadata.size)})
          </CardShadow>
        </div>
      );
    }
    return (
      <div/>
    );
  }
}

export { RelatedDatasetFilter };
