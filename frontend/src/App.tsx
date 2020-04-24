import React from 'react';
import { generateRandomId } from './utils';
import * as api from './api/rest';
import { VerticalLogo, HorizontalLogo, CenteredHorizontalLogo } from './Logo';
import {
  AdvancedSearchBar,
  FilterType,
} from './components/AdvancedSearchBar/AdvancedSearchBar';
import { DateFilter } from './components/DateFilter/DateFilter';
import { RelatedFileFilter } from './components/RelatedFileFilter/RelatedFileFilter';
import { RelatedDatasetFilter } from './components/RelatedFileFilter/RelatedDatasetFilter';
import { GeoSpatialFilter } from './components/GeoSpatialFilter/GeoSpatialFilter';
import { FilterContainer } from './components/FilterContainer/FilterContainer';
import { SourceFilter } from './components/SourceFilter/SourceFilter';
import { SearchBar } from './components/SearchBar/SearchBar';
import { SearchState } from './components/SearchResults/SearchState';
import { SearchResults } from './components/SearchResults/SearchResults';
import {
  SearchResult,
  SearchResponse,
  FilterVariables,
  TemporalVariable,
  GeoSpatialVariable,
} from './api/types';
import { Chip, ChipGroup } from './components/Chip/Chip';
import { MainMenu } from './components/MainMenu/MainMenu';
import { BrowserRouter as Router, Switch, Route } from 'react-router-dom';
import * as Icon from 'react-feather';
import { Upload } from './components/Upload/Upload';
import { Statistics } from './components/Statistics/Statistics';

interface Filter {
  id: string;
  type: FilterType;
  title: string;
  icon: Icon.Icon;
  hidden: boolean;
  component: JSX.Element;
  state?: FilterVariables | File | string[];
}

interface AppState {
  query: string;
  filters: Filter[];
  searchState: SearchState;
  searchResponse?: SearchResponse;
  searchQuery?: api.SearchQuery;
  sources: string[];
}

class SearchApp extends React.Component<{}, AppState> {
  constructor(props: AppState) {
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

  componentDidMount() {
    this.fetchSources();
  }

  async fetchSources() {
    try {
      this.setState({ sources: await api.sources });
    } catch (e) {
      console.error('Unable to fetch list of sources:', e);
    }
  }

  resetQuery() {
    this.setState(this.initialState());
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
    state?: TemporalVariable | GeoSpatialVariable | File | string[]
  ) {
    const filter = this.state.filters.find(f => f.id === filterId);
    if (filter) {
      filter.state = state;
      this.setState({ filters: [...this.state.filters] });
    } else {
      console.warn(
        `Requested to update filter state with id=[${filterId} which does not exist.]`
      );
    }
  }

  handleAddFilter(filterType: FilterType, datasetResult?: SearchResult) {
    const filters = this.state.filters;
    if (
      filterType === FilterType.RELATED_FILE &&
      filters.filter(f => f.type === FilterType.RELATED_FILE).length > 0
    ) {
      return;
    }
    if (
      filterType === FilterType.SOURCE &&
      filters.filter(f => f.type === FilterType.SOURCE).length > 0
    ) {
      return;
    }
    const filterId = generateRandomId();
    const filterComponent = datasetResult ? this.createFilterComponent(filterId, filterType, datasetResult)
                            : this.createFilterComponent(filterId, filterType);
    filters.push({
      id: filterId,
      type: filterType,
      hidden: false,
      ...filterComponent,
    });
    this.setState({ filters: [...filters] });
    if (datasetResult) {
      console.warn('Searching for related datasets to : ' + datasetResult.id);
      const queryByDatasetID = 'related:' + datasetResult.id
      this.updateFilterState(filterId, [queryByDatasetID]);
    }
   
  }

  submitQuery() {
    if (this.validQuery()) {
      const filterVariables = this.state.filters
        .filter(f => f.type !== FilterType.RELATED_FILE)
        .filter(f => f.type !== FilterType.SOURCE)
        .filter(f => f && f.state)
        .map(f => f.state as FilterVariables);

      const files: File[] = this.state.filters
        .filter(f => f.type === FilterType.RELATED_FILE)
        .map(f => f.state as File);

      const sources: string[][] = this.state.filters
        .filter(f => f.type === FilterType.SOURCE)
        .map(f => f.state as string[]);

      const query: api.SearchQuery = {
        query: this.state.query,
        filters: filterVariables,
        sources: sources[0],
        file: files[0],
      };

      this.setState({
        searchQuery: query,
        searchState: SearchState.SEARCH_REQUESTING,
        filters: this.state.filters.map(f => {
          f.hidden = true;
          console.log(f);
          return f;
        }),
      });

      api
        .search(query)
        .then(response => {
          if (response.status === api.RequestResult.SUCCESS && response.data) {
            this.setState({
              searchState: SearchState.SEARCH_SUCCESS,
              searchResponse: {
                results: response.data.results,
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
  }

  createFilterComponent(
    filterId: string,
    filterType: FilterType,
    datasetResult?: SearchResult
  ): { title: string; component: JSX.Element; icon: Icon.Icon } {
    switch (filterType) {
      case FilterType.TEMPORAL:
        return {
          title: 'Temporal',
          icon: Icon.Calendar,
          component: (
            <DateFilter
              key={`datefilter-${filterId}`}
              onDateFilterChange={d => this.updateFilterState(filterId, d)}
            />
          ),
        };
      case FilterType.RELATED_FILE:
        return {
          title: 'Related File',
          icon: Icon.File,
          component: (
            <RelatedFileFilter
              key={`relatedfilefilter-${filterId}`}
              onSelectedFileChange={f => this.updateFilterState(filterId, f)}
            />
          ),
        };
      case FilterType.RELATED_DATASETS:
          return {
            title: 'Related File',
            icon: Icon.File,
            component: (
              <RelatedDatasetFilter
                key={`relatedfilefilter-${filterId}`}
                datasetResult={datasetResult}
              />
            ),
          };
      case FilterType.GEO_SPATIAL:
        return {
          title: 'Geo-Spatial',
          icon: Icon.MapPin,
          component: (
            <GeoSpatialFilter
              key={`geospatialfilter-${filterId}`}
              onSelectCoordinates={c => this.updateFilterState(filterId, c)}
            />
          ),
        };
      case FilterType.SOURCE:
        return {
          title: 'Sources',
          icon: Icon.Database,
          component: (
            <SourceFilter
              key={`sourcefilter-${filterId}`}
              sources={this.state.sources}
              onSourcesChange={s => this.updateFilterState(filterId, s)}
            />
          ),
        };
      default:
        throw new Error(`Received not supported filter type=[${filterType}]`);
    }
  }

  toggleFilter(itemId: string) {
    const filter = this.state.filters.find(f => f.id === itemId);
    if (filter) {
      filter.hidden = !filter.hidden;
      this.setState({ filters: [...this.state.filters] });
    }
  }

  renderFilters() {
    return this.state.filters
      .filter(f => !f.hidden)
      .map(f => (
        <FilterContainer
          key={`filter-container-${f.id}`}
          title={f.title}
          onClose={() => this.removeFilter(f.id)}
        >
          {f.component}
        </FilterContainer>
      ));
  }

  renderCompactFilters() {
    return (
      <ChipGroup>
        {this.state.filters.map(f => (
          <Chip
            key={`filter-chip-${f.id}`}
            icon={f.icon}
            label={f.title}
            onClose={() => this.removeFilter(f.id)}
            onEdit={() => this.toggleFilter(f.id)}
          />
        ))}
      </ChipGroup>
    );
  }

  render() {
    return (
      <>
        {this.state.searchQuery ? (
          <>
            <div className="row">
              <div className="col-md">
                <div className="d-flex flex-row mt-4 mb-3">
                  <div>
                    <HorizontalLogo onClick={() => this.resetQuery()} />
                  </div>
                  <div className="ml-4">
                    <SearchBar
                      key={'search-bar'}
                      value={this.state.query}
                      active={this.validQuery()}
                      onQueryChange={q => this.setState({ query: q })}
                      onSubmitQuery={() => this.submitQuery()}
                    />
                    <AdvancedSearchBar
                      onAddFilter={type => this.handleAddFilter(type)}
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
                  searchQuery={this.state.searchQuery}
                  searchState={this.state.searchState}
                  searchResponse={this.state.searchResponse}
                  onAddFilter={(type, datasetResult) => this.handleAddFilter(type, datasetResult)}
                />
              </div>
            </div>
          </>
        ) : (
          <div>
            <VerticalLogo />
            <SearchBar
              key={'search-bar'}
              value={this.state.query}
              active={this.validQuery()}
              onQueryChange={q => this.setState({ query: q })}
              onSubmitQuery={() => this.submitQuery()}
            />
            <AdvancedSearchBar
              onAddFilter={type => this.handleAddFilter(type)}
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

class App extends React.Component<{}, AppState> {
  render() {
    return (
      <div className="container-fluid">
        <Router>
          <Switch>
            <Route
              path="/upload"
              render={routeProps => (
                <>
                  <MainMenu />
                  <CenteredHorizontalLogo
                    onClick={() => routeProps.history.push('/')}
                  />
                  <Upload />
                </>
              )}
            />
            <Route
              path="/statistics"
              render={routeProps => (
                <>
                  <MainMenu />
                  <CenteredHorizontalLogo
                    onClick={() => routeProps.history.push('/')}
                  />
                  <Statistics />
                </>
              )}
            />
            <Route path="/">
              <MainMenu />
              <SearchApp />
            </Route>
          </Switch>
        </Router>
      </div>
    );
  }
}

export { App };
