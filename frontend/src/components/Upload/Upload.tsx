import React from 'react';
import { Tabs, Tab, TabContent, TabPane } from '../ui/Tabs/Tabs';
import * as Icon from 'react-feather';
import * as api from '../../api/rest';
import { SubmitButton } from '../ui/Button/Button';

interface Validation {
  valid: boolean;
  errors: {
    file?: string;
    name?: string;
    address?: string;
  };
}

interface UploadFormState {
  name: string;
  description: string;
  address?: string;
  validation: Validation;
  submitting: boolean;
  customFields?: api.CustomFields;
  customValues: Map<string, string>;
}

interface UploadFormProps {
  type: 'upload' | 'url';
  onFormSubmit: (data: api.UploadData) => Promise<boolean>;
}

class UploadForm extends React.PureComponent<UploadFormProps, UploadFormState> {
  fileInput: React.RefObject<HTMLInputElement> = React.createRef();

  constructor(props: UploadFormProps) {
    super(props);
    this.state = this.initialState();
    api.customFields().then(customFields => {
      this.setState(prevState => {
        // Filter the values
        const customFieldIds = Object.getOwnPropertyNames(customFields);
        const customValues: Map<string, string> = new Map();
        prevState.customValues.forEach((value, field) => {
          if (customFieldIds.includes(field)) {
            customValues.set(field, value);
          }
        });

        return { customFields, customValues };
      });
    });
    this.onFormSubmit = this.onFormSubmit.bind(this);
  }

  initialState() {
    return {
      address: '',
      name: '',
      description: '',
      validation: { valid: true, errors: {} },
      submitting: false,
      customValues: new Map(),
    };
  }

  async onFormSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const validation = this.validate();
    if (!validation.valid) {
      this.setState({ validation });
    } else {
      this.setState({ submitting: true });
      const success = await this.props.onFormSubmit({
        file: this.getFile(),
        address: this.state.address ? this.state.address : undefined,
        name: this.state.name,
        description: this.state.description,
        customFields: this.state.customValues,
      });
      if (success) {
        this.setState(this.initialState());
      } else {
        this.setState({ submitting: false });
      }
    }
  }

  validate(): Validation {
    const errors: { [field: string]: string } = {};

    if (this.props.type === 'upload' && !this.getFile()) {
      errors.file = 'File is required';
    }
    if (this.props.type === 'url' && !this.state.address) {
      errors.address = 'URL is required';
    }
    if (!this.state.name) {
      errors.name = 'Name is required';
    }
    const valid = errors.file || errors.name || errors.address ? false : true;
    return { valid, errors };
  }

  getFile() {
    if (
      this.fileInput.current &&
      this.fileInput.current.files &&
      this.fileInput.current.files.length > 0
    ) {
      return this.fileInput.current.files[0];
    } else {
      return undefined;
    }
  }

  render() {
    let customFields = <p>Loading custom fields...</p>;
    if (this.state.customFields !== undefined) {
      customFields = (
        <>
          {Object.entries(this.state.customFields).map(([f, opts]) => (
            <FormGroup for={`upload-${f}`} label={opts.label} key={f}>
              <input
                type="text"
                id={`upload-${f}`}
                className="form-control"
                value={this.state.customValues.get(f) || ''}
                onChange={e => {
                  const value = e.target.value;
                  this.setState(prevState => {
                    const customValues = new Map(prevState.customValues);
                    customValues.set(f, value);
                    return { customValues };
                  });
                }}
              />
            </FormGroup>
          ))}
        </>
      );
    }
    return (
      <form onSubmit={this.onFormSubmit}>
        {this.props.type === 'upload' && (
          <FormGroup for="upload-file" label="CSV file">
            <input
              type="file"
              id="upload-file"
              className={`form-control-file${
                this.state.validation.errors.file ? ' is-invalid' : ''
              }`}
              ref={this.fileInput}
            />
            {this.state.validation.errors.file && (
              <div className="invalid-feedback">
                {this.state.validation.errors.file}
              </div>
            )}
          </FormGroup>
        )}
        {this.props.type === 'url' && (
          <FormGroup for="url-address" label="URL to CSV file">
            <input
              type="text"
              id="url-address"
              className={`form-control${
                this.state.validation.errors.address ? ' is-invalid' : ''
              }`}
              placeholder="Type here a URL that points to a CSV file"
              value={this.state.address}
              onChange={e => this.setState({ address: e.target.value })}
            />
            {this.state.validation.errors.address && (
              <div className="invalid-feedback">
                {this.state.validation.errors.address}
              </div>
            )}
          </FormGroup>
        )}
        <FormGroup for="upload-name" label="Name">
          <input
            type="text"
            id="upload-name"
            className={`form-control${
              this.state.validation.errors.name ? ' is-invalid' : ''
            }`}
            placeholder="Type here the name of the dataset"
            value={this.state.name}
            onChange={e => this.setState({ name: e.target.value })}
          />
          {this.state.validation.errors.name && (
            <div className="invalid-feedback">
              {this.state.validation.errors.name}
            </div>
          )}
        </FormGroup>
        <FormGroup for="upload-description" label="Description:">
          <textarea
            className="form-control"
            id="upload-description"
            placeholder="Type here the dataset description"
            value={this.state.description}
            onChange={e => this.setState({ description: e.target.value })}
          />
        </FormGroup>
        {customFields}
        <FormGroup>
          <SubmitButton label="Upload" loading={this.state.submitting} />
        </FormGroup>
      </form>
    );
  }
}

interface FormGroupProps {
  for: string;
  label: string;
}

class FormGroup extends React.Component<FormGroupProps | {}> {
  hasLabel(props: FormGroupProps | {}): props is FormGroupProps {
    return (this.props as FormGroupProps).for !== undefined;
  }

  render() {
    return (
      <div className="form-group row">
        {this.hasLabel(this.props) && (
          <label htmlFor={this.props.for} className="col-sm-2 col-form-label">
            {this.props.label}
          </label>
        )}
        <div
          className={`col-sm-10${
            this.hasLabel(this.props) ? '' : ' offset-sm-2'
          }`}
        >
          {this.props.children}
        </div>
      </div>
    );
  }
}

interface UploadState {
  state: 'upload' | 'url';
  name?: string;
  description?: string;
  file?: string | File;
  success?: boolean;
  failed?: string;
}

class Upload extends React.PureComponent<{}, UploadState> {
  constructor(props: {}) {
    super(props);
    this.state = { state: 'upload' };
    this.onFormSubmit = this.onFormSubmit.bind(this);
  }

  async onFormSubmit(data: api.UploadData) {
    this.setState({ success: undefined, failed: undefined });
    try {
      const result = await api.upload(data);
      if (result.status === 200) {
        this.setState({ success: true });
        return true;
      }
      this.setState({ failed: `Error ${result.status}: ${result.statusText}` });
    } catch (e) {
      this.setState({ failed: `${e}` });
    }
    return false;
  }

  render() {
    return (
      <div className="container container-body">
        <h1>Upload a new dataset</h1>

        <p>
          This form allows you to manually add new datasets to Auctus’ search
          index. Uploaded datasets will be searchable by anybody using Auctus.
        </p>

        {this.state.failed && (
          <div className="alert alert-danger" role="alert">
            Unexpected error: failed to submit dataset ({this.state.failed}).
          </div>
        )}
        {this.state.success && (
          <div className="alert alert-success" role="alert">
            File submitted successfully.
          </div>
        )}

        <Tabs>
          <Tab
            selected={this.state.state === 'upload'}
            onClick={() => this.setState({ state: 'upload' })}
          >
            <Icon.File className="feather-lg" /> Upload
          </Tab>
          <Tab
            selected={this.state.state === 'url'}
            onClick={() => this.setState({ state: 'url' })}
          >
            <Icon.Link2 className="feather-lg" /> Direct URL
          </Tab>
        </Tabs>

        <TabContent>
          <TabPane active={true} id="upload">
            <UploadForm
              type={this.state.state}
              onFormSubmit={this.onFormSubmit}
            />
          </TabPane>
        </TabContent>
      </div>
    );
  }
}

export { Upload };
