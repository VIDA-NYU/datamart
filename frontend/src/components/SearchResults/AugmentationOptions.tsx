import * as React from 'react';
import * as Icon from 'react-feather';
import {
  SearchResult,
  AugmentationInfo,
  ColumnAggregations,
} from '../../api/types';
import * as api from '../../api/rest';
import { SearchQuery } from '../../api/rest';
import { triggerFileDownload, cloneObject } from '../../utils';
import { JoinColumnsSelector } from '../JoinColumnsSelector/JoinColumnsSelector';
import { ColumnBadge, SimpleColumnBadge } from '../Badges/Badges';

interface AugmentationOptionsProps {
  hit: SearchResult;
  searchQuery: SearchQuery;
}

interface AugmentationOptionsState {
  checked: {
    [id: string]: boolean;
  };
  columnAggregations?: ColumnAggregations;
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

class AugmentationOptions extends React.PureComponent<
  AugmentationOptionsProps,
  AugmentationOptionsState
> {
  constructor(props: AugmentationOptionsProps) {
    super(props);
    const initialState: AugmentationOptionsState = { checked: {} };
    const columns = getAugmentationColumns(props.hit.augmentation);
    columns.forEach(c => {
      initialState.checked[c.idx.toString()] = true;
    });
    this.state = initialState;
    this.handleColumnSelectionChange = this.handleColumnSelectionChange.bind(
      this
    );
  }

  handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const checked = this.state.checked;
    checked[input.value] = !checked[input.value];
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
    columnAggregations?: ColumnAggregations
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

    return augmentation;
  }

  async submitAugmentationForm(hit: SearchResult) {
    // find indexes of columns that are checked
    const checkedIndexes = this.findIndexesOfCheckedColumn();

    // augmentation form is only shown when a file was provided as search
    // input, so the file should never be undefined at this point. The search
    // API always returns hit.augmentation when the file was provided.
    const original = hit.augmentation!;
    const file = this.props.searchQuery.file!;

    // clone object because we need to modify it for sending as an API parameter
    const task = cloneObject(hit);

    // adjust augmentation info to use only the checked indexes
    task.augmentation = this.createAugmentationInfo(
      original,
      checkedIndexes,
      this.state.columnAggregations
    );

    console.log('submit', task);

    api.augment(file, task).then(response => {
      const zipFile = response.data;
      if (zipFile) {
        triggerFileDownload(zipFile, 'augmentation.zip');
      } else {
        console.error('Augment API call returned invalid file: ', zipFile);
      }
    });
  }

  renderAugmentButton(hit: SearchResult, type: string) {
    const btnActive = this.findIndexesOfCheckedColumn().length > 0;
    const btnClass = `btn btn-sm btn-outline-primary mt-2${
      btnActive ? '' : ' disabled'
    }`;
    const btnOnClick = btnActive
      ? () => this.submitAugmentationForm(hit)
      : undefined;
    return (
      <button className={btnClass} onClick={btnOnClick}>
        <Icon.Download className="feather" /> Merge{' '}
        <span style={{ textTransform: 'uppercase' }}>({type}) </span>
        &amp; Download
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
        <div>
          <JoinColumnsSelector
            hit={hit}
            onChange={this.handleColumnSelectionChange}
          />
        </div>
        <div>{this.renderAugmentButton(hit, type)}</div>
      </div>
    );
  }
}

export { AugmentationOptions };
