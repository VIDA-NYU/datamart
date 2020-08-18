import React from 'react';
import DatePicker from 'react-datepicker';
import 'react-datepicker/dist/react-datepicker.css';
import './DateFilter.css';
import {TemporalResolution, TemporalVariable} from '../../api/types';

interface DateFilterProps {
  className?: string;
  state?: TemporalVariable;
  onDateFilterChange: (state: TemporalVariable) => void;
}

class DateFilter extends React.PureComponent<DateFilterProps> {
  constructor(props: DateFilterProps) {
    super(props);
    this.state = {};
  }

  onStartDateChange(date: Date | null) {
    const start = date ? date : undefined;
    this.props.onDateFilterChange({
      type: 'temporal_variable',
      ...this.props.state,
      start: this.formatDate(start),
    });
  }

  onEndDateChange(date: Date | null) {
    const end = date ? date : undefined;
    this.props.onDateFilterChange({
      type: 'temporal_variable',
      ...this.props.state,
      end: this.formatDate(end),
    });
  }

  onGranularityChange(granularity: string) {
    this.props.onDateFilterChange({
      type: 'temporal_variable',
      ...this.props.state,
      granularity,
    });
  }

  formatDate(date?: Date) {
    return date ? date.toISOString().substring(0, 10) : undefined;
  }

  parseDate(date?: string) {
    return date ? new Date(date) : undefined;
  }

  render() {
    return (
      <div
        className={`input-group${
          this.props.className ? ` ${this.props.className}` : ''
        }`}
      >
        <div className="d-inline">
          <span className="ml-2 mr-1">Start: </span>
          <DatePicker
            selected={this.parseDate(this.props.state?.start)}
            onChange={e => this.onStartDateChange(e)}
          />
        </div>
        <div className="d-inline">
          <span className="ml-2 mr-1">End: </span>
          <DatePicker
            selected={this.parseDate(this.props.state?.end)}
            onChange={e => this.onEndDateChange(e)}
          />
        </div>
        <div className="d-inline">
          <span className="ml-2 mr-1">Granularity: </span>
          <select
            value={this.props.state?.granularity}
            onChange={e => this.onGranularityChange(e.target.value)}
          >
            {Object.values(TemporalResolution).map(value => (
              <option value={value} key={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
      </div>
    );
  }
}

export {DateFilter};
