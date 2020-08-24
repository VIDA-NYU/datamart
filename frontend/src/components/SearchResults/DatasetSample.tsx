import React from 'react';
import {useTable, Column, ColumnInstance, HeaderGroup} from 'react-table';
import {SearchResult, ColumnMetadata} from '../../api/types';
import './DatasetSample.css';
import {VegaLite} from 'react-vega';
import {TopLevelSpec as VlSpec} from 'vega-lite';
import {BadgeGroup} from '../Badges/Badges';

const classMapping: {[key: string]: string} = {
  text: 'semtype-text',
  boolean: 'semtype-boolean',
  enumeration: 'semtype-enumeration',
  identifier: 'semtype-identifier',
  latitude: 'semtype-latitude',
  longitude: 'semtype-longitude',
  datetime: 'semtype-datetime',
};

enum tableViews {
  COMPACT = 'COMPACT',
  DETAIL = 'DETAIL',
  COLUMN = 'COLUMN',
}

function typeName(type: string) {
  return type
    .replace('http://schema.org/', '')
    .replace('https://metadata.datadrivendiscovery.org/types/', '');
}

function SemanticTypeBadge(props: {type: string; column?: ColumnMetadata}) {
  const label = typeName(props.type);
  const semtypeClass = classMapping[label.toLowerCase()];
  const spanClass = semtypeClass
    ? `badge badge-pill semtype ${semtypeClass}`
    : 'badge badge-pill semtype';
  const tempResolution =
    label.toLowerCase() === 'datetime' &&
    props.column &&
    props.column.temporal_resolution
      ? ' ' + props.column.temporal_resolution
      : '';
  return (
    <span className={spanClass}>{label + tempResolution.toUpperCase()}</span>
  );
}

function TypeBadges(props: {column: ColumnMetadata}) {
  return (
    <>
      <BadgeGroup>
        <span className="badge badge-pill badge-primary">
          {typeName(props.column.structural_type)}
        </span>
        {props.column.semantic_types.map(c => (
          <SemanticTypeBadge
            type={c}
            column={props.column}
            key={`sem-type-badge-${c}`}
          />
        ))}
      </BadgeGroup>
    </>
  );
}

function getEncoding(typePlot: string | undefined) {
  const yContent = {
    field: 'count',
    type: 'quantitative',
    title: null,
  };
  if (typePlot === 'histogram_numerical') {
    return {
      y: yContent,
      x: {
        title: null,
        bin: {binned: true},
        field: 'bin_start',
        type: 'quantitative',
        axis: null,
      },
      x2: {
        field: 'bin_end',
      },
      tooltip: [
        {field: 'bin_start', title: 'start', type: 'quantitative'},
        {field: 'bin_end', title: 'end', type: 'quantitative'},
      ],
    };
  } else if (typePlot === 'histogram_temporal') {
    return {
      y: yContent,
      x: {
        title: null,
        bin: {binned: true},
        field: 'date_start',
        type: 'temporal',
        utc: true,
        axis: null,
      },
      x2: {
        field: 'date_end',
      },
      tooltip: [
        {field: 'date_start', title: 'start', type: 'temporal'},
        {field: 'date_end', title: 'end', type: 'temporal'},
      ],
    };
  } else if (typePlot === 'histogram_categorical') {
    return {
      y: yContent,
      x: {
        title: null,
        field: 'bin',
        type: 'ordinal',
        axis: null,
        sort: {order: 'descending', field: 'count'},
      },
      tooltip: {field: 'bin', type: 'ordinal'},
    };
  } else if (typePlot === 'histogram_text') {
    return {
      y: {
        field: 'bin',
        type: 'ordinal',
        title: null,
      },
      x: {
        title: null,
        field: 'count',
        type: 'quantitative',
        sort: {order: 'descending', field: 'count'},
        axis: null,
      },
      tooltip: [
        {field: 'bin', type: 'ordinal'},
        {field: 'count', type: 'quantitative'},
      ],
    };
  } else {
    console.log('Unknown plot type ', typePlot);
    return;
  }
}

function getSpecification(typePlot: string | undefined): VlSpec {
  const specification = {
    width: '120',
    height: '120',
    data: {name: 'values'},
    encoding: getEncoding(typePlot),
    mark: 'bar',
    background: 'transparent',
  };
  return specification as VlSpec;
}

function VegaPlot(props: {
  columnMetadata: ColumnMetadata;
  column: ColumnInstance<string[]>;
  isHeader: boolean;
}) {
  const dataVega = props.columnMetadata.plot?.data;
  const plot = (
    <VegaLite
      spec={getSpecification(props.columnMetadata.plot?.type)}
      data={{values: dataVega}}
      actions={false}
    />
  );
  const message = <p className="small text-muted">Nothing to show.</p>;
  if (dataVega) {
    return props.isHeader ? (
      <th scope="col" {...props.column.getHeaderProps()}>
        {plot}
      </th>
    ) : (
      <td> {plot} </td>
    );
  } else {
    return props.isHeader ? (
      <th
        scope="col"
        {...props.column.getHeaderProps()}
        className="text-center"
        style={{verticalAlign: 'middle'}}
      >
        {message}
      </th>
    ) : (
      <td> {message} </td>
    );
  }
}

function TableColumnView(props: {
  headerGroups: Array<HeaderGroup<string[]>>;
  hit: SearchResult;
}) {
  return (
    <tbody>
      {props.headerGroups[0].headers.map((column, i) => {
        const columnStatistics = (
          <td style={{minWidth: 200}}>
            <ul style={{listStyle: 'none', columnCount: 2, columnGap: 10}}>
              {props.hit.metadata.columns[i].num_distinct_values && (
                <li>Unique Values:</li>
              )}
              {props.hit.metadata.columns[i].stddev && <li>Std Deviation:</li>}
              {props.hit.metadata.columns[i].mean && <li>Mean:</li>}
              {props.hit.metadata.columns[i].num_distinct_values && (
                <li>{props.hit.metadata.columns[i].num_distinct_values}</li>
              )}
              {props.hit.metadata.columns[i].stddev && (
                <li>{props.hit.metadata.columns[i].stddev?.toFixed(2)}</li>
              )}
              {props.hit.metadata.columns[i].mean && (
                <li>{props.hit.metadata.columns[i].mean?.toFixed(2)}</li>
              )}
            </ul>
          </td>
        );
        return (
          <tr key={'column' + i} {...column.getHeaderProps()}>
            <td>
              <b>{column.render('Header')} </b>
            </td>
            <td>
              <TypeBadges column={props.hit.metadata.columns[i]} />
            </td>
            <VegaPlot
              key={`bodyPlot_${i}`}
              columnMetadata={props.hit.metadata.columns[i]}
              column={column}
              isHeader={false}
            />
            {columnStatistics}
          </tr>
        );
      })}
    </tbody>
  );
}

// Compact and Detail view share the same body content. Just the header will change.
function TableCompactDetailView(props: {tableProps: TableProps}) {
  const {columns, data, hit, typeView} = props.tableProps;
  const {getTableBodyProps, headerGroups, rows, prepareRow} = useTable({
    columns,
    data,
  });
  return (
    <>
      <thead>
        {headerGroups.map(headerGroup => (
          // We disable eslint here because the props created by react-table
          // functions used below already include the jsx-key.
          // eslint-disable-next-line react/jsx-key
          <tr {...headerGroup.getHeaderGroupProps()}>
            {headerGroup.headers.map((column, i) => (
              // eslint-disable-next-line react/jsx-key
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
                <TypeBadges column={hit.metadata.columns[i]} />
              </th>
            ))}
          </tr>
        ))}
        {typeView === tableViews.DETAIL &&
          headerGroups.map(headerGroup => (
            // eslint-disable-next-line react/jsx-key
            <tr {...headerGroup.getHeaderGroupProps()}>
              {headerGroup.headers.map((column, i) => (
                <VegaPlot
                  key={`headerPlot_${i}`}
                  columnMetadata={hit.metadata.columns[i]}
                  column={column}
                  isHeader={true}
                />
              ))}
            </tr>
          ))}
      </thead>
      {/* eslint-disable-next-line react/jsx-key */}
      <tbody {...getTableBodyProps()}>
        {rows.map(row => {
          prepareRow(row);
          return (
            // eslint-disable-next-line react/jsx-key
            <tr {...row.getRowProps()}>
              {row.cells.map(cell => {
                // eslint-disable-next-line react/jsx-key
                return <td {...cell.getCellProps()}>{cell.render('Cell')}</td>;
              })}
            </tr>
          );
        })}
      </tbody>
    </>
  );
}

interface TableProps {
  columns: Array<Column<string[]>>;
  data: string[][];
  hit: SearchResult;
  typeView: tableViews;
}

function Table(props: TableProps) {
  const {columns, data, hit, typeView} = props;
  const {getTableProps, headerGroups} = useTable({
    columns,
    data,
  });
  return (
    <table {...getTableProps()} className="table table-hover small">
      {typeView === tableViews.COLUMN ? (
        <TableColumnView headerGroups={headerGroups} hit={hit} />
      ) : (
        <TableCompactDetailView tableProps={props} />
      )}
    </table>
  );
}

interface TableSampleProps {
  hit: SearchResult;
}
interface TableSampleState {
  typeView: tableViews;
}

class DatasetSample extends React.PureComponent<
  TableSampleProps,
  TableSampleState
> {
  constructor(props: TableSampleProps) {
    super(props);
    this.state = {typeView: tableViews.COMPACT};
  }
  updateTypeView(view: tableViews) {
    this.setState({typeView: view});
  }

  render() {
    const {hit} = this.props;
    const sample = hit.sample;
    const headers = sample[0];
    const rows = sample.slice(1, sample.length);

    const columns = headers.map((h, i) => ({
      Header: h,
      accessor: (row: string[]) => row[i],
    }));

    return (
      <div className="mt-2">
        <h6>Dataset Sample:</h6>
        <div>
          <div
            className="btn-group btn-group-sm"
            role="group"
            aria-label="Dataset samples table"
            style={{float: 'initial', marginBottom: '-8px'}}
          >
            <button
              type="button"
              className={`btn btn-secondary ${
                this.state.typeView === tableViews.COMPACT ? 'active' : ''
              }`}
              onClick={() => this.updateTypeView(tableViews.COMPACT)}
            >
              Compact View
            </button>
            <button
              type="button"
              className={`btn btn-secondary ${
                this.state.typeView === tableViews.DETAIL ? 'active' : ''
              }`}
              onClick={() => this.updateTypeView(tableViews.DETAIL)}
            >
              Detail View
            </button>
            <button
              type="button"
              className={`btn btn-secondary ${
                this.state.typeView === tableViews.COLUMN ? 'active' : ''
              }`}
              onClick={() => this.updateTypeView(tableViews.COLUMN)}
            >
              Column View
            </button>
          </div>
          <div className="mt-2">
            <Table
              columns={columns}
              data={rows}
              hit={hit}
              typeView={this.state.typeView}
            />
          </div>
        </div>
      </div>
    );
  }
}

export {DatasetSample};
