import React from 'react';
import { Link } from 'react-router-dom';
import * as api from '../../api/rest';
import { DatasetTypeBadge, BadgeGroup } from '../Badges/Badges';
import moment from 'moment';

interface StatisticsState {
  failed?: string;
  status?: api.Status;
}

class Statistics extends React.PureComponent<{}, StatisticsState> {
  timer: number | undefined;

  constructor(props: {}) {
    super(props);
    this.state = {};
  }

  componentDidMount() {
    this.fetchStatus();
    // update status every so often
    this.timer = setInterval(() => {
      this.fetchStatus();
    }, 60 * 1000);
  }

  componentWillUnmount() {
    clearInterval(this.timer);
  }

  async fetchStatus() {
    try {
      this.setState({ status: await api.status(), failed: undefined });
    } catch (e) {
      this.setState({ failed: `${e}` });
    }
  }

  renderLatest() {
    return (
      <div className="list-group mb-4">
        {this.state.status &&
          this.state.status.recent_discoveries.map(d => (
            <div
              key={`latest-${d.id}`}
              className="list-group-item list-group-item-action flex-column align-items-start"
            >
              <div className="d-flex w-100 justify-content-between">
                <div className="d-flex flex-column">
                  <h5 className="mb-0">
                    <Link
                      to={`/?q=${encodeURIComponent(
                        JSON.stringify({ query: d.id })
                      )}`}
                      style={{ textDecoration: 'none' }}
                    >
                      {d.name}
                    </Link>
                  </h5>
                  <span className="small">{d.discoverer}</span>
                  <span>
                    <b>Dataset ID:</b> {d.id}
                  </span>
                </div>
                <div>
                  <span
                    className="badge badge-light badge-pill"
                    title={moment(d.profiled).format('MMMM Do YYYY, h:mm:ss a')}
                  >
                    {moment(d.profiled).fromNow()}
                  </span>
                </div>
              </div>
              {d.types && d.types.length > 0 && (
                <BadgeGroup>{d.types.map(t => DatasetTypeBadge(t))}</BadgeGroup>
              )}
            </div>
          ))}
      </div>
    );
  }

  render() {
    return (
      <div className="container container-body">
        <h1>Latest indexed datasets</h1>
        <p>Following is a list of the latest datasets indexed by Auctus.</p>
        {this.state.failed && (
          <div className="alert alert-danger" role="alert">
            Failed to fetch statistics ({this.state.failed}).
          </div>
        )}
        {this.renderLatest()}
      </div>
    );
  }
}

export { Statistics };
