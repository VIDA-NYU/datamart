import React from 'react';
import { Link, match } from 'react-router-dom';
import { History, Location } from 'history';
import { generateRandomId } from '../../utils';
import * as api from '../../api/rest';
import { Session } from '../../api/types';
import { VerticalLogo, HorizontalLogo } from '../Logo/Logo';
import {
  AdvancedSearchBar,
  FilterType,
} from '../AdvancedSearchBar/AdvancedSearchBar';
import { DateFilter } from '../DateFilter/DateFilter';
import { RelatedFileFilter } from '../RelatedFileFilter/RelatedFileFilter';
import { GeoSpatialFilter } from '../GeoSpatialFilter/GeoSpatialFilter';
import { FilterContainer } from '../FilterContainer/FilterContainer';
import { SourceFilter } from '../SourceFilter/SourceFilter';
import { SearchBar } from '../SearchBar/SearchBar';
import { SearchState } from '../SearchResults/SearchState';
import { SearchResults } from '../SearchResults/SearchResults';
import {
  SearchResponse,
  FilterVariables,
  TemporalVariable,
  GeoSpatialVariable,
  RelatedFile,
} from '../../api/types';
import { Chip, ChipGroup } from '../Chip/Chip';
import * as Icon from 'react-feather';
import { aggregateResults } from '../../api/augmentation';

interface Filter {
  id: string;
  type: FilterType;
  hidden: boolean;
  state?: FilterVariables | RelatedFile | string[];
}

interface SearchAppState {
  query: string;
  filters: Filter[];
  searchState: SearchState;
  searchResponse?: SearchResponse;
  searchQuery?: api.SearchQuery;
  sources: string[];
  session?: Session;
}

interface SearchAppProps {
  history: History;
  match: match;
  location: Location;
}

class SearchApp extends React.Component<SearchAppProps, SearchAppState> {
  constructor(props: SearchAppProps) {
    super(props);
    this.state = this.initialState();
  }

  initialState() {
    return {
      query: '',
      searchQuery: undefined,
      searchResponse: undefined,
      searchState: SearchState.CLEAN,
      filters: [],
      sources: api.DEFAULT_SOURCES,
    };
  }

  static filtersToQuery(state: SearchAppState): api.SearchQuery {
    const filterVariables = state.filters
      .filter(f => f.type !== FilterType.RELATED_FILE)
      .filter(f => f.type !== FilterType.SOURCE)
      .filter(f => f && f.state)
      .map(f => f.state as FilterVariables);

    let relatedFiles: RelatedFile[] = state.filters
      .filter(f => f.type === FilterType.RELATED_FILE)
      .map(f => f.state as RelatedFile);
    if (state.session?.data_token) {
      relatedFiles = [
        {
          kind: 'localFile',
          name: 'session input',
          token: state.session.data_token,
        },
      ];
    }

    const sources: string[][] = state.filters
      .filter(f => f.type === FilterType.SOURCE)
      .map(f => f.state as string[]);

    const query: api.SearchQuery = {
      query: state.query,
      filters: filterVariables,
      sources: sources[0],
      relatedFile: relatedFiles[0],
    };
    return query;
  }

  static queryToFilters(
    query: api.SearchQuery
  ): { keywords: string; filters: Filter[] } {
    const filters: Filter[] = [];
    if (query.filters) {
      query.filters.forEach(v => {
        let type;
        if (v.type === 'geospatial_variable') {
          type = FilterType.GEO_SPATIAL;
        } else if (v.type === 'temporal_variable') {
          type = FilterType.TEMPORAL;
        } else {
          console.error('Unrecognized query variable ', v);
          return;
        }
        filters.push({
          id: generateRandomId(),
          type,
          hidden: false,
          state: v,
        });
      });
    }
    if (query.sources) {
      filters.push({
        id: generateRandomId(),
        type: FilterType.SOURCE,
        hidden: false,
        state: query.sources,
      });
    }
    if (query.relatedFile) {
      filters.push({
        id: generateRandomId(),
        type: FilterType.RELATED_FILE,
        hidden: false,
        state: query.relatedFile,
      });
    }
    return { keywords: query.query || '', filters };
  }

  static getDerivedStateFromProps(
    props: SearchAppProps,
    state: SearchAppState
  ) {
    const { location } = props;
    const params = new URLSearchParams(location.search);
    // Get session from URL
    const s = params.get('session');
    let session: Session | undefined = undefined;
    if (s) {
      session = JSON.parse(decodeURIComponent(s)) || undefined;
      if (session && session.session_id) {
        return {
          session: { ...session, system_name: session.system_name || 'TA3' },
        };
      }
    }
    return { session: undefined };
  }

  updateSearchStateFromUrlParams(location: Location) {
    const params = new URLSearchParams(location.search);
    const q = params.get('q');
    if (q) {
      const query: api.SearchQuery = JSON.parse(decodeURIComponent(q));
      if (query) {
        // Update state to match
        let { keywords, filters } = SearchApp.queryToFilters(query);
        if (this.state.session?.data_token) {
          filters = filters.filter(f => f.type !== FilterType.RELATED_FILE);
        }
        this.setState(
          {
            query: keywords,
            filters,
          },
          () => {
            // Submit search
            this.fetchSearchResults(query);
          }
        );
      } else {
        this.setState(this.initialState());
      }
    } else {
      this.setState(this.initialState());
    }
  }

  componentDidUpdate(prevProps: SearchAppProps) {
    if (this.state.searchQuery) {
      document.body.classList.add('searchresults');
    } else {
      document.body.classList.remove('searchresults');
    }
    const { location } = this.props;
    if (location !== prevProps.location) {
      this.updateSearchStateFromUrlParams(this.props.location);
    }
  }

  componentDidMount() {
    this.fetchSources();
    if (this.state.searchQuery) {
      document.body.classList.add('searchresults');
    }
    this.updateSearchStateFromUrlParams(this.props.location);
  }

  componentWillUnmount() {
    document.body.classList.remove('searchresults');
  }

  async fetchSources() {
    try {
      this.setState({ sources: await api.sources() });
    } catch (e) {
      console.error('Unable to fetch list of sources:', e);
    }
  }

  removeFilter(filterId: string) {
    this.setState({
      filters: this.state.filters.filter(f => f.id !== filterId),
    });
  }

  validQuery() {
    if (this.state.query && this.state.query.length > 0) return true;
    if (this.state.filters.filter(f => f.state).length > 0) return true;
    return false;
  }

  updateFilterState(
    filterId: string,
    state?: TemporalVariable | GeoSpatialVariable | RelatedFile | string[]
  ) {
    this.setState(prevState => {
      let found = false;
      const filters = prevState.filters.map(filter => {
        if (filter.id === filterId) {
          found = true;
          return { ...filter, state };
        } else {
          return filter;
        }
      });
      if (!found) {
        console.warn(
          `Requested to update filter state with id=[${filterId} which does not exist.]`
        );
      }
      return { filters };
    });
  }

  handleAddFilter(filterType: FilterType) {
    this.setState(prevState => {
      if (
        filterType === FilterType.RELATED_FILE ||
        filterType === FilterType.SOURCE
      ) {
        // Can only have one of those
        if (prevState.filters.filter(f => f.type === filterType).length > 0) {
          return { filters: prevState.filters }; // No change
        }
      }
      const filterId = generateRandomId();
      const filter = {
        id: filterId,
        type: filterType,
        hidden: false,
      };
      return { filters: [filter, ...prevState.filters] };
    });
  }

  submitQuery() {
    if (this.validQuery()) {
      const query = SearchApp.filtersToQuery(this.state);

      // pushes the query into the URL, which will trigger fetching the search results
      const q = encodeURIComponent(JSON.stringify(query));
      let url = `${this.props.match.url}?q=${q}`;
      if (this.state.session) {
        const s = encodeURIComponent(JSON.stringify(this.state.session));
        url += `&session=${s}`;
      }
      this.props.history.push(url);
    }
  }

  fetchSearchResults(query: api.SearchQuery) {
    this.setState({
      searchQuery: query,
      searchState: SearchState.SEARCH_REQUESTING,
      filters: this.state.filters.map(f => ({ ...f, hidden: true })),
    });

    api
      .search(query)
      .then(response => {
        if (response.status === api.RequestResult.SUCCESS && response.data) {
          this.setState({
            searchState: SearchState.SEARCH_SUCCESS,
            searchResponse: {
              results: aggregateResults(response.data.results),
            },
          });
        } else {
          this.setState({ searchState: SearchState.SEARCH_FAILED });
        }
      })
      .catch(() => {
        this.setState({ searchState: SearchState.SEARCH_FAILED });
      });
  }

  toggleFilter(filterId: string) {
    this.setState(prevState => {
      const filters = this.state.filters.map(f => {
        if (f.id === filterId) {
          return { ...f, hidden: !f.hidden };
        } else {
          return f;
        }
      });
      return { filters };
    });
  }

  onSearchRelated(relatedFile: RelatedFile) {
    this.setState(prevState => {
      const prevFilters = this.state.filters;
      const relatedFileFilters = prevFilters.filter(
        f => f.type === FilterType.RELATED_FILE
      );
      let filters;
      if (relatedFileFilters.length > 0) {
        // Update existing filter
        filters = prevFilters.map(filter => {
          if (filter.id === relatedFileFilters[0].id) {
            return {
              ...relatedFileFilters[0],
              state: relatedFile,
            };
          } else {
            return filter;
          }
        });
      } else {
        // Add new filter
        const filterId = generateRandomId();
        const filter: Filter = {
          id: filterId,
          type: FilterType.RELATED_FILE,
          hidden: false,
          state: relatedFile,
        };
        filters = [...prevFilters, filter];
      }
      return { filters };
    }, this.submitQuery);
  }

  renderFilters() {
    return this.state.filters
      .filter(filter => !filter.hidden)
      .map(filter => {
        let title = undefined,
          component = undefined;
        switch (filter.type) {
          case FilterType.TEMPORAL:
            title = 'Temporal';
            component = (
              <DateFilter
                onDateFilterChange={d => this.updateFilterState(filter.id, d)}
                state={filter.state as TemporalVariable | undefined}
              />
            );
            break;
          case FilterType.RELATED_FILE:
            title = 'Related File';
            component = (
              <RelatedFileFilter
                onSelectedFileChange={f => this.updateFilterState(filter.id, f)}
                state={filter.state as RelatedFile | undefined}
              />
            );
            break;
          case FilterType.GEO_SPATIAL:
            title = 'Geo-Spatial';
            component = (
              <GeoSpatialFilter
                state={filter.state as GeoSpatialVariable | undefined}
                onSelectCoordinates={c => this.updateFilterState(filter.id, c)}
              />
            );
            break;
          case FilterType.SOURCE:
            title = 'Sources';
            component = (
              <SourceFilter
                sources={this.state.sources}
                checkedSources={filter.state as string[] | undefined}
                onSourcesChange={s => this.updateFilterState(filter.id, s)}
              />
            );
            break;
          default:
            throw new Error(
              `Received not supported filter type=[${filter.type}]`
            );
        }
        return (
          <FilterContainer
            key={`filter-container-${filter.id}`}
            title={title}
            onClose={() => this.removeFilter(filter.id)}
          >
            {component}
          </FilterContainer>
        );
      });
  }

  renderCompactFilters() {
    const filters = this.state.filters.map(filter => {
      let icon = undefined,
        title = undefined;
      switch (filter.type) {
        case FilterType.TEMPORAL:
          title = 'Temporal';
          icon = Icon.Calendar;
          break;
        case FilterType.RELATED_FILE:
          title = 'Related File';
          icon = Icon.File;
          break;
        case FilterType.GEO_SPATIAL:
          title = 'Geo-Spatial';
          icon = Icon.MapPin;
          break;
        case FilterType.SOURCE:
          title = 'Sources';
          icon = Icon.Database;
          break;
        default:
          throw new Error(
            `Received not supported filter type=[${filter.type}]`
          );
      }
      return (
        <Chip
          key={`filter-chip-${filter.id}`}
          icon={icon}
          label={title}
          onClose={() => this.removeFilter(filter.id)}
          onEdit={() => this.toggleFilter(filter.id)}
        />
      );
    });
    if (this.state.session?.data_token) {
      filters.push(
        <Chip
          key={`filter-chip-session-file`}
          icon={Icon.File}
          label={`File From ${this.state.session.system_name}`}
        />
      );
    }
    return <ChipGroup>{filters}</ChipGroup>;
  }

  render() {
    const { searchQuery, searchState, searchResponse, session } = this.state;

    return (
      <>
        {searchQuery ? (
          <>
            <div className="row">
              <div className="col-md">
                <div className="d-flex flex-row mt-2 mb-1">
                  <div>
                    <Link
                      to={
                        session
                          ? `/?session=${JSON.stringify(this.state.session)}`
                          : '/'
                      }
                      style={{ textDecoration: 'none' }}
                    >
                      <HorizontalLogo />
                    </Link>
                  </div>
                  <div className="ml-4">
                    <SearchBar
                      value={this.state.query}
                      active={this.validQuery()}
                      onQueryChange={q => this.setState({ query: q })}
                      onSubmitQuery={() => this.submitQuery()}
                    />
                    <AdvancedSearchBar
                      onAddFilter={type => this.handleAddFilter(type)}
                      relatedFileEnabled={!session?.data_token}
                    />
                  </div>
                </div>
              </div>
            </div>
            <div className="row" style={{ width: 780 }}>
              <div className="col-md-12 mb-3">
                {this.renderCompactFilters()}
              </div>
              <div className="col-md-12">{this.renderFilters()}</div>
            </div>
            <div className="row">
              <div className="col-md-12">
                <SearchResults
                  searchQuery={searchQuery}
                  searchState={searchState}
                  searchResponse={searchResponse}
                  session={session}
                  onSearchRelated={this.onSearchRelated.bind(this)}
                />
              </div>
            </div>
          </>
        ) : (
          <div>
            <VerticalLogo />
            <SearchBar
              value={this.state.query}
              active={this.validQuery()}
              onQueryChange={q => this.setState({ query: q })}
              onSubmitQuery={() => this.submitQuery()}
            />
            <AdvancedSearchBar
              onAddFilter={type => this.handleAddFilter(type)}
              relatedFileEnabled={!session?.data_token}
            />
            <div style={{ maxWidth: 1000, margin: '1.5rem auto' }}>
              {this.renderFilters()}
            </div>
          </div>
        )}
      </>
    );
  }
}

export { SearchApp };
