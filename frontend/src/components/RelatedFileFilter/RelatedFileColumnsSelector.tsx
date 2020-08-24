import React from 'react';
import {Metadata, TabularVariable} from '../../api/types';
import {BadgeGroup, ColumnBadge, BadgeButton} from '../Badges/Badges';
import './RelatedFileColumnsSelector.css';

export function RelatedFileColumnsSelector(props: {
  profile: Metadata;
  selectedTabularVars: TabularVariable;
  onAdd: (colName: string) => void;
  onRemove: (colName: string) => void;
  onUpdateTabularVariables: (colIndexes: number[]) => void;
}) {
  const {profile, selectedTabularVars} = props;
  return (
    <div className="row">
      <div className="col-sm border-right">
        <b className="mt-2 ">Available columns:</b>
        {selectedTabularVars.columns.length !== profile.columns.length && (
          <button
            className="text-muted small label-button"
            onClick={() =>
              props.onUpdateTabularVariables(
                Array.from(new Array(profile.columns.length).keys())
              )
            }
          >
            Add All
          </button>
        )}
        <br />
        <span className="small">
          {selectedTabularVars.columns.length === profile.columns.length
            ? 'All columns were selected.'
            : 'Select which columns should be included in the search.'}
        </span>
        <BadgeGroup>
          {profile.columns
            .filter(
              (unit, index) =>
                selectedTabularVars &&
                !selectedTabularVars.columns.includes(index)
            )
            .map((c, i) => (
              <ColumnBadge
                key={`badge-bin-${'uniqueBinId'}-column-${i}`}
                cornerButton={BadgeButton.ADD}
                column={c}
                onClick={() => props.onAdd(c.name)}
              />
            ))}
        </BadgeGroup>
      </div>
      <div className="col-sm">
        <b className="mt-2">Selected columns:</b>
        {selectedTabularVars.columns.length > 0 && (
          <button
            className="text-muted small label-button"
            onClick={() => props.onUpdateTabularVariables([])}
          >
            Remove All
          </button>
        )}
        <br />
        {selectedTabularVars.columns.length === 0 ? (
          <span className="small">
            {' '}
            Select <b className="label danger"> at least one column </b> to be
            included in the search.
          </span>
        ) : (
          <span className="small">
            These columns will be included in the search.
          </span>
        )}
        <BadgeGroup>
          {selectedTabularVars.columns
            .map(index => profile && profile.columns[index])
            .map(
              (c, i) =>
                c && (
                  <ColumnBadge
                    key={`badge-bin-${'uniqueBinId'}-column-${i}`}
                    cornerButton={BadgeButton.REMOVE}
                    column={c}
                    onClick={() => props.onRemove(c.name)}
                  />
                )
            )}
        </BadgeGroup>
      </div>
    </div>
  );
}
