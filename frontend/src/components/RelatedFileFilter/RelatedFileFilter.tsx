import React from 'react';
import Dropzone from 'react-dropzone';
import { CardShadow, CardButton } from '../visus/Card/Card';
import { formatSize, shallowEqual } from '../../utils';
import { Metadata, RelatedFile } from '../../api/types';
import { ProfileResult, profile, metadata } from '../../api/rest';

interface RelatedFileFilterState {
  profile?: Metadata;
}

interface RelatedFileFilterProps {
  onSelectedFileChange: (relatedFile: RelatedFile) => void;
  state?: RelatedFile;
}

class RelatedFileFilter extends React.PureComponent<
  RelatedFileFilterProps,
  RelatedFileFilterState
> {
  profileQuery?: Promise<ProfileResult | Metadata>;
  profileQueryFile?: RelatedFile;

  constructor(props: RelatedFileFilterProps) {
    super(props);
    this.state = {};
    if (props.state) {
      this.getProfile(props.state);
    }
  }

  getProfile(relatedFile: RelatedFile): Promise<ProfileResult | Metadata> {
    let profileQuery: Promise<ProfileResult | Metadata>;
    if (relatedFile.kind === 'localFile') {
      profileQuery = profile(relatedFile.token);
    } else if (relatedFile.kind === 'searchResult') {
      profileQuery = metadata(relatedFile.datasetId);
    } else {
      throw new Error('Invalid RelatedFile prop');
    }
    this.profileQuery = profileQuery;
    this.profileQueryFile = relatedFile;
    profileQuery.then(p => {
      // Check that this is still the current query
      // (JavaScript can't cancel promises)
      if (this.profileQuery === profileQuery) {
        this.setState({ profile: p });
      }
    });
    return profileQuery;
  }

  componentDidUpdate(prevProps: RelatedFileFilterProps) {
    if (!this.props.state) {
      this.profileQuery = undefined;
    } else if (
      !this.profileQueryFile ||
      !shallowEqual(this.props.state, this.profileQueryFile)
    ) {
      // Get profile for this file (asynchronously)
      this.getProfile(this.props.state);
    }
  }

  handleSelectedFile(acceptedFiles: File[]) {
    if (acceptedFiles.length > 0) {
      const file = acceptedFiles[0];
      const profileQuery = profile(file);
      this.profileQuery = profileQuery;
      profileQuery.then(p => {
        // Check that this is still the current query
        // (JavaScript can't cancel promises)
        if (this.profileQuery === profileQuery) {
          const relatedFile: RelatedFile = {
            kind: 'localFile',
            token: p.token,
            name: file.name,
            fileSize: file.size,
          };
          this.profileQueryFile = relatedFile;
          this.setState({ profile: p });
          this.props.onSelectedFileChange(relatedFile);
        }
      });
    }
  }

  render() {
    const maxSize = 100 * 1024 * 1024; // maximum file size
    const relatedFile = this.props.state;
    if (relatedFile) {
      let columns = '';
      if (this.state.profile !== undefined) {
        columns = this.state.profile.columns.map(c => c.name).join(', ');
      }
      return (
        <div>
          <CardShadow height={'auto'}>
            <span className="font-weight-bold">Selected dataset:</span>{' '}
            {relatedFile.name}
            {relatedFile.fileSize !== undefined
              ? ' (' + formatSize(relatedFile.fileSize) + ')'
              : undefined}
            {columns ? ` (${columns})` : undefined}
          </CardShadow>
        </div>
      );
    }
    return (
      <div>
        <CardShadow>
          <Dropzone
            multiple={false}
            minSize={0}
            maxSize={maxSize}
            onDrop={acceptedFiles => this.handleSelectedFile(acceptedFiles)}
          >
            {({
              getRootProps,
              getInputProps,
              isDragActive,
              isDragReject,
              rejectedFiles,
            }) => {
              const isFileTooLarge =
                rejectedFiles.length > 0 && rejectedFiles[0].size > maxSize;
              return (
                <CardButton {...getRootProps()}>
                  <div className="dropzone dropzone-container text-center">
                    <input {...getInputProps()} />
                    {!isDragActive && (
                      <>
                        <h6 className="d-block text-bold">
                          Search with a dataset instead of text
                        </h6>
                        <span>
                          We will find datasets that can be joined with your
                          dataset.
                          <br />
                          Click (or drag and drop a file) here to upload a CSV
                          file.
                        </span>
                      </>
                    )}
                    {isDragActive && !isDragReject && 'Drop it here!'}
                    {isDragReject && (
                      <span className="text-danger">
                        File type not accepted. Only CSV files are supported.
                      </span>
                    )}
                    {isFileTooLarge && (
                      <div className="text-danger">File is too large.</div>
                    )}
                  </div>
                </CardButton>
              );
            }}
          </Dropzone>
        </CardShadow>
      </div>
    );
  }
}

export { RelatedFileFilter };
