import React from 'react';
import { useDrop } from 'react-dnd';
import * as Icon from 'react-feather';

const ItemType = 'badge';

const functionBinStyle = (background: string): React.CSSProperties => ({
  border: '1px solid #c0c0c0',
  padding: '.5rem',
  margin: '0.25rem',
  minHeight: '100px',
  minWidth: '100px',
  verticalAlign: 'middle',
  backgroundColor: background,
});

interface FunctionBinProps {
  fn: string;
  label?: string;
}

const FunctionBin: React.FC<FunctionBinProps> = ({ fn, label }) => {
  const [{ canDrop, isOver }, drop] = useDrop({
    accept: ItemType,
    drop: () => ({ name: fn }),
    collect: monitor => ({
      isOver: monitor.isOver(),
      canDrop: monitor.canDrop(),
    }),
  });
  const isActive = canDrop && isOver;
  return (
    <div ref={drop} style={functionBinStyle('#f0f0f0')}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          flexDirection: 'column',
          textAlign: 'center',
          height: '100%',
        }}
      >
        <span>
          {isActive ? (
            'Release!'
          ) : (
            <>
              {label ? (
                <i className="small text-primary">{label}</i>
              ) : (
                <>
                  {fn.toUpperCase()}(
                  <Icon.Hash className="feather text-primary" />)
                </>
              )}
            </>
          )}
        </span>
      </div>
    </div>
  );
};

export { FunctionBin };
