import React from 'react';
import * as Icon from 'react-feather';

interface DatasetTypeFilterProps {
  datasetTypes: string[];
  checkedDatasetTypes?: string[];
  onDatasetTypeChange: (checkedDatasetTypes: string[]) => void;
}

class DatasetTypeFilter extends React.PureComponent<DatasetTypeFilterProps> {
  handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    let found = false;
    const checkedDatasetTypes = this.getCheckedDatasetTypes().filter(s => {
      if (s === input.value) {
        found = true;
        return false; // Remove it from checked (uncheck it)
      } else {
        return true;
      }
    });
    if (!found) {
      checkedDatasetTypes.push(input.value); // Add it to checked
    }
    this.props.onDatasetTypeChange(checkedDatasetTypes);
  }

  getCheckedDatasetTypes() {
    // If 'checkedDatasetTypes' prop is undefined, consider all data types checked
    return this.props.checkedDatasetTypes === undefined
      ? this.props.datasetTypes
      : this.props.checkedDatasetTypes;
  }

  setCheckedStateForAll(checked: boolean) {
    if (checked) {
      this.props.onDatasetTypeChange(this.props.datasetTypes);
    } else {
      this.props.onDatasetTypeChange([]);
    }
  }

  render() {
    const datasetTypes: { [datasetType: string]: boolean } = {};
    this.props.datasetTypes.forEach(datasetType => {
      datasetTypes[datasetType] = false;
    });
    this.getCheckedDatasetTypes().forEach(datasetType => {
      datasetTypes[datasetType] = true;
    });
    const datasetTypesList = Object.entries(datasetTypes);
    datasetTypesList.sort((a, b) => {
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
        {datasetTypesList.map(([type, checked]) => (
          <div className="form-check ml-2" key={`div-${type}`}>
            <input
              className="form-check-input"
              type="checkbox"
              value={type}
              checked={checked}
              id={`check-box-${type}`}
              onChange={e => this.handleChange(e)}
            />
            <label className="form-check-label" htmlFor={`check-box-${type}`}>
              {type.charAt(0).toUpperCase() + type.slice(1)}
            </label>
          </div>
        ))}
      </>
    );
  }
}

export { DatasetTypeFilter };
