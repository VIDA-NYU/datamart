import React from 'react';
import { useDrop, useDrag, DragSourceMonitor } from 'react-dnd';
import { DndProvider } from 'react-dnd';
import Backend from 'react-dnd-html5-backend';
import { ColumnBadge, BadgeGroup } from '../Badges/Badges';
import { SearchResult, ColumnMetadata } from '../../api/types';
import { FunctionBin } from './FunctionBin';

const ItemType = 'badge';

const NumberAggFunctions = ['first', 'mean', 'sum', 'max', 'min', 'count'];
const StringAggFunctions = ['first'];
const AllAggFunctions = '_all';

const badgeBinStyle = (background: string): React.CSSProperties => ({
  border: '1px solid #c0c0c0',
  padding: '.25rem',
  minHeight: '100px',
  backgroundColor: background,
});

interface BadgeBinProps {
  uniqueBinId: string;
  columns?: AggColumn[];
}

const BadgeBin: React.FC<BadgeBinProps> = ({ uniqueBinId, columns }) => {
  const [{ canDrop, isOver, column }, drop] = useDrop({
    accept: ItemType,
    // drop: () => ({ name: 'BadgeBin' }),
    collect: monitor => ({
      isOver: monitor.isOver(),
      canDrop: monitor.canDrop(),
      column: monitor.getItem()?.column as ColumnMetadata | null,
    }),
  });

  let background = 'transparent';
  const isActive = canDrop && isOver;
  if (isActive) {
    // green-ish, when badge is over the target bin
    background = '#859f2850';
  } else if (canDrop) {
    // gray-ish, while badge in being dragged toward the target bin
    background = '#f0f0f0';
  }

  const isDragging = column !== null && canDrop;
  const isStringColumn = column && column.structural_type.endsWith('Text');
  const isNumberColumn = column && !column.structural_type.endsWith('Text');
  return (
    <div className="d-flex flex-column">
      <b className="mt-2">Included after merge:</b>
      <span className="small">
        You final dataset with have the following columns in addition to the
        original columns.
      </span>
      <div ref={drop} style={badgeBinStyle(background)}>
        <div className={isDragging ? 'd-flex flex-wrap' : 'd-none'}>
          <div className={isStringColumn ? 'd-flex flex-wrap' : 'd-none'}>
            {StringAggFunctions.map(fn => (
              <FunctionBin fn={fn} key={`bin-${uniqueBinId}-fn-${fn}`} />
            ))}
          </div>
          <div className={isNumberColumn ? 'd-flex flex-wrap' : 'd-none'}>
            {NumberAggFunctions.map(fn => (
              <FunctionBin fn={fn} key={`bin-${uniqueBinId}-fn-${fn}`} />
            ))}
          </div>
          <FunctionBin fn={AllAggFunctions} label="All functions" />
        </div>
        {isActive ? (
          <span className="small">Release to drop!</span>
        ) : columns && columns.length > 0 ? (
          <BadgeGroup>
            {columns.map((c, i) => (
              <ColumnBadge
                key={`badge-bin-${uniqueBinId}-column-${i}`}
                column={c.column}
                function={c.agg_function}
              />
            ))}
          </BadgeGroup>
        ) : (
          <span className="small">
            Drag columns here to include them in the final merged dataset.
          </span>
        )}
      </div>
    </div>
  );
};

interface DraggableBadgeProps {
  column: ColumnMetadata;
  onDrop: (column: ColumnMetadata, agg_function: string) => void;
}

const DraggableBadge: React.FC<DraggableBadgeProps> = ({ column, onDrop }) => {
  const [{ isDragging }, drag] = useDrag({
    item: { column: column, type: ItemType },
    end: (item: ColumnMetadata | undefined, monitor: DragSourceMonitor) => {
      const dropResult = monitor.getDropResult();
      if (item && dropResult) {
        onDrop(column, dropResult.name);
      }
    },
    collect: monitor => ({
      isDragging: monitor.isDragging(),
    }),
  });
  const opacity = isDragging ? 0.4 : 1;
  return (
    <div ref={drag} style={{ cursor: 'move', opacity }}>
      <ColumnBadge column={column} />
    </div>
  );
};

interface AggColumn {
  column: ColumnMetadata;
  agg_function: string;
}

interface JoinColumnsSelectorProps {
  hit: SearchResult;
}

interface JoinColumnsSelectorState {
  columns: AggColumn[];
}

class JoinColumnsSelector extends React.Component<
  JoinColumnsSelectorProps,
  JoinColumnsSelectorState
> {
  constructor(props: JoinColumnsSelectorProps) {
    super(props);
    this.state = { columns: [] };
  }

  addColumn(column: ColumnMetadata, agg_function: string) {
    this.setState({
      columns: [...this.state.columns, { column, agg_function }],
    });
  }

  handleDrop(column: ColumnMetadata, agg_function: string) {
    if (!agg_function || agg_function === AllAggFunctions) {
      const functionNames = column.structural_type.endsWith('Text')
        ? StringAggFunctions // string column
        : NumberAggFunctions; // number column
      functionNames.forEach(fn => this.addColumn(column, fn));
    } else {
      this.addColumn(column, agg_function);
    }
  }

  render() {
    const { hit } = this.props;
    if (!hit.augmentation || hit.augmentation.type === 'none') {
      return null;
    }
    return (
      <DndProvider backend={Backend}>
        <div className="d-flex flex-column">
          <b className="mt-2">Available columns:</b>
          <span className="small">
            Select which columns should be added to the final merged dataset.
          </span>
          <BadgeGroup>
            {hit.metadata.columns.map((c, i) => (
              <DraggableBadge
                key={`dragbadge-${i}-${hit.id}`}
                column={c}
                onDrop={(c, fn) => this.handleDrop(c, fn)}
              />
            ))}
          </BadgeGroup>
          <BadgeBin columns={this.state.columns} uniqueBinId={hit.id} />
        </div>
      </DndProvider>
    );
  }
}

export { JoinColumnsSelector };
