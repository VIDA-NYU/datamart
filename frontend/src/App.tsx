import React from 'react';
import { CenteredHorizontalLogo } from './components/Logo/Logo';
import { MainMenu } from './components/MainMenu/MainMenu';
import { BrowserRouter as Router, Link, Switch, Route } from 'react-router-dom';
import { Upload } from './components/Upload/Upload';
import { Statistics } from './components/Statistics/Statistics';
import { SearchApp } from './components/SearchApp/SearchApp';

class App extends React.Component {
  render() {
    return (
      <div className="container-fluid">
        <Router>
          <Switch>
            <Route
              path="/upload"
              render={routeProps => (
                <>
                  <MainMenu />
                  <Link to="/" style={{ textDecoration: 'none' }}>
                    <CenteredHorizontalLogo />
                  </Link>
                  <Upload />
                </>
              )}
            />
            <Route
              path="/statistics"
              render={routeProps => (
                <>
                  <MainMenu />
                  <Link to="/" style={{ textDecoration: 'none' }}>
                    <CenteredHorizontalLogo />
                  </Link>
                  <Statistics />
                </>
              )}
            />
            <Route
              path="/"
              render={routeProps => (
                <>
                  <MainMenu />
                  <SearchApp history={routeProps.history} match={routeProps.match} />
                </>
              )}
            />
          </Switch>
        </Router>
      </div>
    );
  }
}

export { App };
