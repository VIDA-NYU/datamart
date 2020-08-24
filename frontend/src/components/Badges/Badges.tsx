import React from 'react';
import * as Icon from 'react-feather';
import './Badges.css';
import {IconAbc} from './IconAbc';
import {ColumnMetadata} from '../../api/types';

export enum BadgeButton {
  ADD = 'ADD',
  REMOVE = 'REMOVE',
}

export function columnType(column: ColumnMetadata) {
  switch (column.structural_type) {
    case 'http://schema.org/Integer':
    case 'http://schema.org/Float':
      if (
        column.semantic_types.includes('http://schema.org/latitude') ||
        column.semantic_types.includes('http://schema.org/longitude')
      ) {
        return {numerical: true, spatial: true};
      }
      return {numerical: true};
    case 'http://schema.org/Text':
    default:
      if (column.semantic_types) {
        if (column.semantic_types.includes('http://schema.org/DateTime')) {
          return {textual: true, temporal: true};
        }
        if (
          column.semantic_types.includes('http://schema.org/latitude') ||
          column.semantic_types.includes('http://schema.org/longitude')
        ) {
          return {textual: true, spatial: true};
        }
      }
      return {textual: true};
  }
}

function iconForType(types: {
  textual?: boolean;
  temporal?: boolean;
  numerical?: boolean;
  spatial?: boolean;
}) {
  if (types.spatial) {
    return Icon.Globe;
  } else if (types.temporal) {
    return Icon.Calendar;
  } else if (types.numerical) {
    return Icon.Hash;
  } else {
    return IconAbc;
  }
}

export function SpatialBadge() {
  return (
    <span className="badge badge-primary badge-pill">
      <Icon.MapPin className="feather-xs" /> Spatial
    </span>
  );
}

export function TemporalBadge() {
  return (
    <span className="badge badge-info badge-pill">
      <Icon.Calendar className="feather-xs" /> Temporal
    </span>
  );
}

export function NumericalBadge() {
  return (
    <span className="badge badge-number badge-pill">
      <Icon.Hash className="feather-xs" /> Numerical
    </span>
  );
}

export function CategoricalBadge() {
  return (
    <span className="badge badge-categorical badge-pill">
      <Icon.List className="feather-xs" /> Categorical
    </span>
  );
}

export function DatasetTypeBadge(props: {type: string}) {
  const {type} = props;
  if (type === 'spatial') {
    return SpatialBadge();
  } else if (type === 'temporal') {
    return TemporalBadge();
  } else if (type === 'numerical') {
    return NumericalBadge();
  } else if (type === 'categorical') {
    return CategoricalBadge();
  } else {
    console.error('Invalid dataset type: ', type);
    return null;
  }
}

export function SimpleColumnBadge(props: {name: string}) {
  return <span className={'badge badge-pill badge-column'}>{props.name}</span>;
}

export function ColumnBadge(props: {
  column: ColumnMetadata;
  type?: 'categorical' | 'numerical';
  function?: string;
  cornerButton?: BadgeButton;
  onClick?: () => void;
}) {
  let label = props.column.name;
  const cornerButtonSize = 15;
  if (props.function) {
    label = `${props.function.toUpperCase()}(${label})`;
  }
  const types = columnType(props.column);
  const badgeClass = types.numerical ? 'badge-numerical' : 'badge-textual';
  const BadgeIcon = iconForType(types);

  return (
    <span className={`badge badge-pill ${badgeClass}`}>
      <BadgeIcon className="feather-xs-w" />
      {label}
      {props.cornerButton === BadgeButton.ADD && (
        <button
          type="button"
          title="Add this column"
          className="btn btn-link badge-corner-button"
          onClick={() => props.onClick && props.onClick()}
        >
          <Icon.PlusCircle size={cornerButtonSize} />
        </button>
      )}
      {props.cornerButton === BadgeButton.REMOVE && (
        <button
          type="button"
          title="Remove this column"
          className="btn btn-link badge-corner-button"
          onClick={() => props.onClick && props.onClick()}
        >
          <Icon.XCircle size={cornerButtonSize} />
        </button>
      )}
    </span>
  );
}

interface BadgeGroupProps {
  className?: string;
}

export const BadgeGroup: React.FunctionComponent<BadgeGroupProps> = props => (
  <div
    className={`badge-group${props.className ? ` ${props.className} ` : ''}`}
  >
    {props.children}
  </div>
);
