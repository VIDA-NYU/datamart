import React from 'react';
import * as Icon from 'react-feather';
import { ColumnMetadata, TypesCategory, ColumnType } from '../../api/types';
import { useTable, Column } from 'react-table';
import { Loading } from '../visus/Loading/Loading';
import { RequestStatus, ProfileResult } from '../../api/rest';
import { isSubstrInclude, updateLatLonDropdown } from '../../utils';

const classMapping: { [key: string]: string } = {
  text: 'semtype-text',
  boolean: 'semtype-boolean',
  enumeration: 'semtype-enumeration',
  identifier: 'semtype-identifier',
  latitude: 'semtype-latitude',
  longitude: 'semtype-longitude',
  datetime: 'semtype-datetime',
};

function formatTypeName(type: string) {
  return type
    .replace('http://schema.org/', '')
    .replace('https://metadata.datadrivendiscovery.org/types/', '');
}

function SemanticTypeBadge(props: {
  type: string;
  column: ColumnMetadata;
  onRemove: () => void;
}) {
  const label = formatTypeName(props.type);
  const semtypeClass = classMapping[label.toLowerCase()];
  const spanClass = semtypeClass
    ? `inline-flex badge badge-pill semtype ${semtypeClass}`
    : 'inline-flex badge badge-pill semtype';
  const latlonPair =
    (label.toLowerCase() === 'latitude' ||
      label.toLowerCase() === 'longitude') &&
    props.column.latlong_pair
      ? '-(pair' + props.column.latlong_pair + ')'
      : '';

  return (
    <span className={spanClass}>
      {label + latlonPair}
      <button
        type="button"
        title="Remove this annotation"
        className="btn btn-link badge-button"
        onClick={() => props.onRemove()}
      >
        <Icon.XCircle size={11} />
      </button>
    </span>
  );
}

function TypeBadges(props: {
  column: ColumnMetadata;
  columns: ColumnMetadata[];
  onEdit: (value: string, type: TypesCategory) => void;
  onRemove: (value: string) => void;
}) {
  const structuralTypes = [
    ColumnType.TEXT,
    ColumnType.INTEGER,
    ColumnType.FLOAT,
    ColumnType.GEO_POINT,
    ColumnType.GEO_POLYGON,
    ColumnType.MISSING_DATA,
  ];
  let semanticTypes = [
    ColumnType.CATEGORICAL,
    ColumnType.DATE_TIME,
    ColumnType.LATITUDE + '-(pair1)',
    ColumnType.LONGITUDE + '-(pair1)',
    ColumnType.BOOLEAN,
    ColumnType.TEXT,
    ColumnType.ADMIN,
    ColumnType.ID,
  ];

  const usedLat: string[] = [];
  const usedLon: string[] = [];
  props.columns.forEach(col => {
    if (
      isSubstrInclude(col['semantic_types'], ColumnType.LATITUDE) &&
      col['latlong_pair']
    ) {
      usedLat.push(col['latlong_pair']);
    }
    if (
      isSubstrInclude(col['semantic_types'], ColumnType.LONGITUDE) &&
      col['latlong_pair']
    ) {
      usedLon.push(col['latlong_pair']);
    }
  });
  const semanticTypesLat = updateLatLonDropdown(
    usedLat,
    usedLon,
    props.column,
    true
  );
  const semanticTypesLon = updateLatLonDropdown(
    usedLon,
    usedLat,
    props.column,
    false
  );

  if (usedLat.length > 0 || usedLon.length > 0) {
    const semanticTypesTemp = semanticTypes
      .filter(unit => !unit.includes(ColumnType.LONGITUDE))
      .filter(unit => !unit.includes(ColumnType.LATITUDE))
      .concat(semanticTypesLat, semanticTypesLon);
    semanticTypes = semanticTypesTemp.filter((item, pos) => {
      return semanticTypesTemp.indexOf(item) === pos;
    });
  }

  return (
    <>
      <select
        className="bootstrap-select badge badge-pill badge-primary"
        value={props.column.structural_type}
        onChange={e => {
          props.onEdit(e.target.value, TypesCategory.STRUCTURAL);
        }}
      >
        {structuralTypes.map(unit => (
          <option key={unit} value={unit}>
            {formatTypeName(unit)}
          </option>
        ))}
      </select>
      {props.column.semantic_types.map(c => (
        <SemanticTypeBadge
          type={c}
          column={props.column}
          key={`sem-type-badge-${c}`}
          onRemove={() => props.onRemove(c)}
        />
      ))}

      <div>
        <div className="dropdown">
          <button type="button" className="btn btn-link">
            <span className="small">Annotate </span>
            <span className="caret"></span>
          </button>
          <div className="dropdown-content">
            {semanticTypes
              .filter(unit => !props.column.semantic_types.includes(unit))
              .filter(
                unit =>
                  !(
                    isSubstrInclude(
                      props.column.semantic_types,
                      ColumnType.DATE_TIME
                    ) && unit.includes(ColumnType.DATE_TIME)
                  )
              )
              .map(unit => (
                <div
                  key={formatTypeName(unit)}
                  className="menu-link"
                  onClick={() => props.onEdit(unit, TypesCategory.SEMANTIC)}
                >
                  {formatTypeName(unit)}
                </div>
              ))}
          </div>
        </div>
      </div>
    </>
  );
}

// Compact view.

interface TableProps {
  columns: Array<Column<string[]>>;
  data: string[][];
  profiledData: ProfileResult;
  onEdit: (value: string, type: TypesCategory, column: ColumnMetadata) => void;
  onRemove: (value: string, column: ColumnMetadata) => void;
}

function Table(props: TableProps) {
  const { columns, data, profiledData } = props;
  const {
    headerGroups,
    rows,
    getTableProps,
    getTableBodyProps,
    prepareRow,
  } = useTable({
    columns,
    data,
  });
  return (
    <table
      {...getTableProps()}
      className="table table-hover small"
      style={{ height: 100 }}
    >
      <thead>
        {headerGroups.map((headerGroup, i) => (
          <tr {...headerGroup.getHeaderGroupProps()}>
            {headerGroup.headers.map((column, i) => (
              <th
                scope="col"
                {...column.getHeaderProps()}
                style={{
                  position: 'sticky',
                  top: '-1px',
                  background: '#eee',
                  zIndex: 1,
                }}
              >
                {column.render('Header')}
                <br />
                {
                  <TypeBadges
                    column={profiledData.columns[i]}
                    columns={profiledData.columns}
                    onEdit={(value, type) => {
                      props.onEdit(value, type, profiledData.columns[i]);
                    }}
                    onRemove={value =>
                      props.onRemove(value, profiledData.columns[i])
                    }
                  />
                }
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody {...getTableBodyProps()} style={{ height: 100 }}>
        {rows.map((row, i) => {
          prepareRow(row);
          return (
            <tr {...row.getRowProps()}>
              {row.cells.map(cell => {
                return <td {...cell.getCellProps()}>{cell.render('Cell')}</td>;
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

interface ProfileDatasetProps {
  profilingStatus: RequestStatus;
  profiledData?: ProfileResult;
  failedProfiler?: string;
  onEdit: (value: string, type: TypesCategory, column: ColumnMetadata) => void;
  onRemove: (value: string, column: ColumnMetadata) => void;
}
interface DataTable {
  columns: Array<Column<string[]>>;
  rows: string[][];
}

class ProfileDataset extends React.PureComponent<ProfileDatasetProps, {}> {
  getSample(text: string): string[][] {
    const lines = text.split('\n');
    const result = [];
    const headers = lines[0].split(',');
    result.push(headers);
    for (let i = 1; i < lines.length; i++) {
      const row = lines[i].split(',');
      result.push(row);
    }
    return result;
  }

  getDataTable(profiledData: ProfileResult): DataTable {
    const sample = this.getSample(profiledData.sample);
    const headers = sample[0];
    const rows = sample.slice(1, sample.length - 1);
    const columns = headers.map((h, i) => ({
      Header: h,
      accessor: (row: string[]) => row[i],
    }));
    return { columns, rows };
  }

  renderErrorMessage(error?: string) {
    return (
      <>
        <div className="text-danger mt-2 mb-4">
          Unexpected error while profiling data {error && `(${error})`}
        </div>
      </>
    );
  }

  render() {
    const { profiledData, profilingStatus, failedProfiler } = this.props;

    const dataTable = profiledData && this.getDataTable(profiledData);

    return (
      <>
        {profilingStatus === RequestStatus.SUCCESS &&
          profiledData &&
          dataTable && (
            <div style={{ maxHeight: 300, minHeight: 200, overflow: 'auto' }}>
              <Table
                columns={dataTable.columns}
                data={dataTable.rows}
                profiledData={profiledData}
                onEdit={(value, type, updatedColumn) => {
                  this.props.onEdit(value, type, updatedColumn);
                }}
                onRemove={(value, updatedColumn) =>
                  this.props.onRemove(value, updatedColumn)
                }
              />
            </div>
          )}
        {profilingStatus === RequestStatus.IN_PROGRESS && (
          <span className="mr-2">
            <Loading message={`Profiling CSV file ...`} />
          </span>
        )}
        {profilingStatus === RequestStatus.ERROR && (
          <span className="mr-2">
            <>{this.renderErrorMessage(failedProfiler)}</>
          </span>
        )}
      </>
    );
  }
}

export { ProfileDataset };
