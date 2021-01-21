import React from 'react';
import * as Icon from 'react-feather';
import {SearchFacet} from '../../api/types';

interface SourceFilterProps {
  sources: string[];
  checkedSources?: string[];
  onSourcesChange: (checkedSources: string[]) => void;
  facetBuckets?: SearchFacet;
}

class SourceFilter extends React.PureComponent<SourceFilterProps> {
  handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    let found = false;
    const checkedSources = this.getCheckedSources().filter(s => {
      if (s === input.value) {
        found = true;
        return false; // Remove it from checked (uncheck it)
      } else {
        return true;
      }
    });
    if (!found) {
      checkedSources.push(input.value); // Add it to checked
    }
    this.props.onSourcesChange(checkedSources);
  }

  getCheckedSources() {
    // If 'checkedSources' prop is undefined, consider all sources checked
    return this.props.checkedSources === undefined
      ? this.props.sources
      : this.props.checkedSources;
  }

  setCheckedStateForAll(checked: boolean) {
    if (checked) {
      this.props.onSourcesChange(this.props.sources);
    } else {
      this.props.onSourcesChange([]);
    }
  }

  render() {
    const {facetBuckets} = this.props;
    const sources: {[source: string]: boolean} = {};
    this.props.sources.forEach(source => {
      sources[source] = false;
    });
    this.getCheckedSources().forEach(source => {
      sources[source] = true;
    });
    const sourcesList = Object.entries(sources);
    sourcesList.sort((a, b) => {
      // Compare size of bucket
      const ka: number = (facetBuckets && facetBuckets.buckets[a[0]]) || 0;
      const kb: number = (facetBuckets && facetBuckets.buckets[b[0]]) || 0;
      if (ka < kb) {
        return 1;
      } else if (ka > kb) {
        return -1;
      }
      // Compare by name
      if (a[0] < b[0]) {
        return -1;
      } else if (a[0] > b[0]) {
        return 1;
      } else {
        return 0;
      }
    });
    return (
      <>
        <div className="mb-1 mt-1">
          <button
            className="btn-link small ml-2"
            onClick={() => {
              this.setCheckedStateForAll(true);
            }}
          >
            <Icon.CheckSquare className="feather feather-sm mr-1" />
            Select all
          </button>
          <button
            className="btn-link small ml-2"
            onClick={() => {
              this.setCheckedStateForAll(false);
            }}
          >
            <Icon.Square className="feather feather-sm mr-1" />
            Unselect all
          </button>
        </div>
        {sourcesList.map(([source, checked]) => (
          <div className="form-check ml-2" key={`div-${source}`}>
            <input
              className="form-check-input"
              type="checkbox"
              value={source}
              checked={checked}
              id={`check-box-${source}`}
              onChange={e => this.handleChange(e)}
            />
            <label className="form-check-label" htmlFor={`check-box-${source}`}>
              {source}
              {facetBuckets && facetBuckets.buckets[source]
                ? ' (' + facetBuckets.buckets[source] + ')'
                : undefined}
            </label>
          </div>
        ))}
      </>
    );
  }
}

export {SourceFilter};
