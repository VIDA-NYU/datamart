import * as React from 'react';
import * as Icon from 'react-feather';
import {
  SearchResult,
  AugmentationInfo,
  ColumnAggregations,
  Session,
  TemporalResolution,
} from '../../api/types';
import * as api from '../../api/rest';
import { SearchQuery } from '../../api/rest';
import { triggerFileDownload, cloneObject } from '../../utils';
import { JoinColumnsSelector } from '../JoinColumnsSelector/JoinColumnsSelector';
import { ColumnBadge, SimpleColumnBadge } from '../Badges/Badges';

interface AugmentationOptionsProps {
  hit: SearchResult;
  searchQuery: SearchQuery;
  session?: Session;
}

interface AugmentationOptionsState {
  checked: {
    [id: string]: boolean;
  };
  temporalResolution?: TemporalResolution;
  columnAggregations?: ColumnAggregations;
  result?: api.RequestStatus;
}

function getAugmentationColumns(aug?: AugmentationInfo) {
  if (!aug) {
    return [];
  }
  const columns: Array<{
    leftColumn: string;
    rightColumn: string;
    key: string;
    idx: number;
  }> = [];

  for (let idx = 0; idx < aug.left_columns_names.length; idx++) {
    const leftColumn = aug.left_columns_names[idx].join(', ');
    const rightColumn = aug.right_columns_names[idx].join(', ');
    const key = `${leftColumn}, ${rightColumn}`;
    columns.push({ leftColumn, rightColumn, key, idx });
  }
  return columns;
}

function TemporalResolutionSelector(props: {
  resolution: TemporalResolution;
  onChange: (value: TemporalResolution) => void;
}) {
  return (
    <select
      value={props.resolution}
      onChange={e => props.onChange(e.target.value as TemporalResolution)}
    >
      {Object.values(TemporalResolution).map(value => (
        <option value={value} key={value}>
          {value}
        </option>
      ))}
    </select>
  );
}

class AugmentationOptions extends React.PureComponent<
  AugmentationOptionsProps,
  AugmentationOptionsState
> {
  constructor(props: AugmentationOptionsProps) {
    super(props);
    const initialState: AugmentationOptionsState = {
      checked: {},
      temporalResolution: props.hit.augmentation?.temporal_resolution,
    };
    const columns = getAugmentationColumns(props.hit.augmentation);
    columns.forEach((c, index) => {
      initialState.checked[c.idx.toString()] = index === 0 ? true : false;
    });
    this.state = initialState;
    this.handleColumnSelectionChange = this.handleColumnSelectionChange.bind(
      this
    );
    this.handleTemporalResolutionChange = this.handleTemporalResolutionChange.bind(
      this
    );
  }

  handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const checked = Object.assign({}, this.state.checked, {
      [input.value]: !this.state.checked[input.value],
    });
    this.setState({ checked: { ...checked } });
  }

  findIndexesOfCheckedColumn() {
    return Object.entries(this.state.checked)
      .filter(c => c[1] === true)
      .map(c => +c[0]); // cast index back to number
  }

  createAugmentationInfo(
    original: AugmentationInfo,
    checkedIndexes: number[],
    columnAggregations?: ColumnAggregations,
    temporalResolution?: TemporalResolution
  ) {
    // make a copy of the original so we can modify it
    const augmentation = cloneObject(original);

    augmentation.left_columns = [];
    augmentation.left_columns_names = [];
    augmentation.right_columns = [];
    augmentation.right_columns_names = [];

    // copy only the selected indexes from the original
    // augmentation info to our augmentation request
    augmentation.type = original.type;
    for (const i of checkedIndexes) {
      augmentation.left_columns.push(original.left_columns[i]);
      augmentation.left_columns_names.push(original.left_columns_names[i]);
      augmentation.right_columns.push(original.right_columns[i]);
      augmentation.right_columns_names.push(original.right_columns_names[i]);
    }

    augmentation.agg_functions = columnAggregations;
    augmentation.temporal_resolution = temporalResolution;

    return augmentation;
  }

  async submitAugmentationForm(hit: SearchResult) {
    // find indexes of columns that are checked
    const checkedIndexes = this.findIndexesOfCheckedColumn();

    // augmentation form is only shown when a file was provided as search
    // input, so the file should never be undefined at this point. The search
    // API always returns hit.augmentation when the file was provided.
    const original = hit.augmentation!;
    const relatedFile = this.props.searchQuery.relatedFile!;

    // clone object because we need to modify it for sending as an API parameter
    const task = cloneObject(hit);

    // adjust augmentation info to use only the checked indexes
    task.augmentation = this.createAugmentationInfo(
      original,
      checkedIndexes,
      this.state.columnAggregations,
      this.state.temporalResolution
    );

    console.log('submit', task);

    const { session } = this.props;
    api
      .augment(relatedFile, task, session)
      .then(response => {
        this.setState({ result: api.RequestStatus.SUCCESS });
        if (!session) {
          const zipFile = response.data;
          if (zipFile) {
            triggerFileDownload(zipFile, 'augmentation.zip');
          } else {
            console.error('Augment API call returned invalid file: ', zipFile);
          }
        }
      })
      .catch(() => this.setState({ result: api.RequestStatus.ERROR }));
    this.setState({ result: api.RequestStatus.IN_PROGRESS });
  }

  renderAugmentButton(hit: SearchResult, type: string) {
    const { session } = this.props;
    const { result } = this.state;
    let btnActive = this.findIndexesOfCheckedColumn().length > 0;
    let text = `Merge`;
    if (session) {
      if (result === undefined) {
        text = `Merge (${type}) & Add to ${session.system_name}`;
      } else if (result === api.RequestStatus.IN_PROGRESS) {
        text = `Merging...`;
        btnActive = false;
      } else if (result === api.RequestStatus.SUCCESS) {
        text = `Added to ${session.system_name}!`;
        btnActive = false;
      } else if (result === api.RequestStatus.ERROR) {
        text = 'Error merging';
      }
    } else {
      if (result === undefined) {
        text = `Merge (${type}) & Download`;
      } else if (result === api.RequestStatus.IN_PROGRESS) {
        text = `Merging...`;
        btnActive = false;
      } else if (result === api.RequestStatus.SUCCESS) {
        text = `Downloaded`;
        // Keep button active, if user didn't save file
      } else if (result === api.RequestStatus.ERROR) {
        text = 'Error merging';
      }
    }
    const btnClass = `btn btn-sm btn-outline-primary mt-2${
      btnActive ? '' : ' disabled'
    }`;
    const btnOnClick = btnActive
      ? () => this.submitAugmentationForm(hit)
      : undefined;
    return (
      <button className={btnClass} onClick={btnOnClick}>
        <Icon.Download className="feather" /> {text}
      </button>
    );
  }

  renderMergeColumns(
    columns: Array<{
      leftColumn: string;
      rightColumn: string;
      key: string;
      idx: number;
    }>,
    hit: SearchResult
  ) {
    return columns.map((c, i) => {
      const rightMetadata = hit.metadata.columns.find(
        m => m.name === c.rightColumn
      );
      return (
        <div className="form-check ml-2" key={`div-aug-${i}`}>
          <input
            className="form-check-input"
            type="checkbox"
            value={c.idx}
            checked={this.state.checked[c.idx]}
            id={`checkbox-${c.key}`}
            onChange={e => this.handleChange(e)}
          />
          <label className="form-check-label" htmlFor={`checkbox-${c.key}`}>
            <SimpleColumnBadge name={c.leftColumn} />
            <span className="ml-1 mr-1">and</span>
            {rightMetadata ? (
              <ColumnBadge column={rightMetadata} />
            ) : (
              <SimpleColumnBadge name={c.rightColumn} />
            )}
          </label>
        </div>
      );
    });
  }

  handleColumnSelectionChange(columnAggregations: ColumnAggregations) {
    this.setState({ columnAggregations });
  }

  handleTemporalResolutionChange(temporalResolution: TemporalResolution) {
    this.setState({ temporalResolution });
  }

  render() {
    const { hit } = this.props;
    if (!hit.augmentation || hit.augmentation.type === 'none') {
      return null;
    }

    const { type } = hit.augmentation;
    const columns = getAugmentationColumns(hit.augmentation);

    return (
      <div className="d-flex flex-column mt-3">
        <h6>
          Augmentation{' '}
          <span style={{ textTransform: 'uppercase' }}>({type})</span>
        </h6>
        <b>
          <span style={{ textTransform: 'capitalize' }}>{type}</span> on:
        </b>
        {this.renderMergeColumns(columns, hit)}
        {this.state.temporalResolution ? (
          <>
            <b>Temporal resolution:</b>
            <TemporalResolutionSelector
              resolution={this.state.temporalResolution}
              onChange={this.handleTemporalResolutionChange}
            />
          </>
        ) : (
          undefined
        )}
        <div>
          {hit.augmentation && hit.augmentation.type === 'join' && (
            <JoinColumnsSelector
              hit={hit}
              excludeColumns={columns.map(c => c.rightColumn)}
              onChange={this.handleColumnSelectionChange}
            />
          )}
        </div>
        <div>{this.renderAugmentButton(hit, type)}</div>
      </div>
    );
  }
}

export { AugmentationOptions };
