import React from 'react';
import * as Icon from 'react-feather';
import {
  SearchResponse,
  SearchResult,
  RelatedFile,
  InfoBoxType,
  Session,
} from '../../api/types';
import { SearchHit } from './SearchHit';
import { SearchState } from './SearchState';
import { Loading } from '../visus/Loading/Loading';
import { HitInfoBox } from './HitInfoBox';
import { SearchQuery } from '../../api/rest';

interface SearchResultsProps {
  searchQuery: SearchQuery;
  searchState: SearchState;
  searchResponse?: SearchResponse;
  session?: Session;
  onSearchRelated: (relatedFile: RelatedFile) => void;
}

interface SearchResultsState {
  selectedHit?: SearchResult;
  selectedInfoBoxType: InfoBoxType;
}

class SearchResults extends React.PureComponent<
  SearchResultsProps,
  SearchResultsState
> {
  lastSearchResponse?: SearchResponse;

  constructor(props: SearchResultsProps) {
    super(props);
    this.state = { selectedInfoBoxType: InfoBoxType.DETAIL };
  }

  componentDidUpdate() {
    if (this.lastSearchResponse !== this.props.searchResponse) {
      this.setState({
        selectedHit: this.props.searchResponse
          ? this.props.searchResponse.results[0]
          : undefined,
        selectedInfoBoxType: InfoBoxType.DETAIL,
      });
    }
    this.lastSearchResponse = this.props.searchResponse;
  }

  render() {
    const { searchResponse, searchState, searchQuery, session } = this.props;
    const centeredDiv: React.CSSProperties = {
      width: 750,
      textAlign: 'center',
      marginTop: '1rem',
    };
    switch (searchState) {
      case SearchState.SEARCH_REQUESTING: {
        return (
          <div style={centeredDiv}>
            <Loading message="Searching..." />
          </div>
        );
      }
      case SearchState.SEARCH_FAILED: {
        return (
          <div style={centeredDiv}>
            <Icon.XCircle className="feather" />
            &nbsp; An unexpected error occurred. Please try again later.
          </div>
        );
      }
      case SearchState.SEARCH_SUCCESS: {
        if (!(searchResponse && searchResponse.results.length > 0)) {
          return (
            <div style={centeredDiv}>
              <Icon.AlertCircle className="feather text-primary" />
              &nbsp;No datasets found, please try another query.
            </div>
          );
        }

        // TODO: Implement proper results pagination
        const page = 1;
        const k = 20;
        const currentHits = searchResponse.results.slice(
          (page - 1) * k,
          page * k
        );
        const { selectedHit, selectedInfoBoxType } = this.state;
        return (
          <div className="d-flex flex-row">
            <div
              className="col-md-4 px-0 pb-5"
              style={{ maxHeight: '90vh', overflowY: 'scroll' }}
            >
              {currentHits.map((hit, idx) => (
                <SearchHit
                  hit={hit}
                  key={idx}
                  selectedHit={
                    selectedHit && hit.id === selectedHit.id ? true : false
                  }
                  session={session}
                  onSearchHitExpand={hit =>
                    this.setState({
                      selectedHit: hit,
                      selectedInfoBoxType: InfoBoxType.DETAIL,
                    })
                  }
                  onSearchRelated={this.props.onSearchRelated}
                  onAugmentationOptions={hit =>
                    this.setState({
                      selectedHit: hit,
                      selectedInfoBoxType: InfoBoxType.AUGMENTATION,
                    })
                  }
                />
              ))}
            </div>
            {selectedHit && (
              <HitInfoBox
                hit={selectedHit}
                searchQuery={searchQuery}
                infoBoxType={selectedInfoBoxType}
                session={session}
              />
            )}
          </div>
        );
      }
      case SearchState.CLEAN:
      default: {
        return null;
      }
    }
  }
}

export { SearchResults };
