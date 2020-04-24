import './AdvancedSearchBar.css';
import React from 'react';
import * as Icon from 'react-feather';

export enum FilterType {
  TEMPORAL,
  GEO_SPATIAL,
  RELATED_FILE,
  SOURCE,
  RELATED_DATASETS
}

interface AdvancedSearchBarProps {
  onAddFilter: (type: FilterType) => void;
}

class AdvancedSearchBar extends React.PureComponent<AdvancedSearchBarProps> {
  render() {
    return (
      <div className="AdvancedSearchBar">
        <span className="d-inline text-oswald AdvancedSearchBar-title">
          Advanced Search:{' '}
        </span>
        <div
          className="d-inline btn AdvancedSearchBar-item"
          onClick={() => this.props.onAddFilter(FilterType.TEMPORAL)}
        >
          <Icon.Calendar className="feather" />
          <span>Any Date</span>
          <Icon.ChevronDown className="feather" />
        </div>
        <div
          className="d-inline btn AdvancedSearchBar-item"
          onClick={() => this.props.onAddFilter(FilterType.GEO_SPATIAL)}
        >
          <Icon.MapPin className="feather" />
          <span>Any Location</span>
          <Icon.ChevronDown className="feather" />
        </div>
        <div
          className="d-inline btn AdvancedSearchBar-item"
          onClick={() => this.props.onAddFilter(FilterType.RELATED_FILE)}
        >
          <Icon.File className="feather" />
          <span>Related File</span>
          <Icon.ChevronDown className="feather" />
        </div>
        <div
          className="d-inline btn AdvancedSearchBar-item"
          onClick={() => this.props.onAddFilter(FilterType.SOURCE)}
        >
          <Icon.Database className="feather" />
          <span>Source</span>
          <Icon.ChevronDown className="feather" />
        </div>
      </div>
    );
  }
}

export { AdvancedSearchBar };
