import './SearchBar.css';
import React from 'react';
import * as Icon from 'react-feather';

interface SearchBarProps {
  active: boolean;
  placeholder?: string;
  value: string;
  onQueryChange: (query: string) => void;
  onSubmitQuery: () => void;
}

class SearchBar extends React.PureComponent<SearchBarProps> {
  constructor(props: SearchBarProps) {
    super(props);
    this.handleChange = this.handleChange.bind(this);
    this.handleSubmit = this.handleSubmit.bind(this);
  }

  isActive() {
    return this.props.active || this.props.value !== '';
  }

  handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const query = event.target.value;
    this.props.onQueryChange(query);
  }

  handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    this.props.onSubmitQuery();
  }

  render() {
    return (
      <form onSubmit={this.handleSubmit}>
        <div className="input-group SearchBar">
          <input
            type="text"
            name="search"
            className="form-control SearchBar-input"
            placeholder={this.props.placeholder}
            value={this.props.value}
            onChange={this.handleChange}
          />
          <div
            className="input-group-append"
            onClick={() => this.props.onSubmitQuery()}
          >
            <span
              className={`input-group-text SearchBar-icon${
                this.isActive() ? ' SearchBar-icon-active' : ''
              }`}
            >
              <Icon.Search className="feather" />
            </span>
          </div>
        </div>
      </form>
    );
  }
}

export { SearchBar };
