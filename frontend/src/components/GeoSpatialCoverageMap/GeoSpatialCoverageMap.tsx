import React from 'react';
import { generateRandomId } from '../../utils';
import { Map, View, Feature, Overlay } from 'ol/';
import { toStringHDMS } from 'ol/coordinate';
import { defaults as interactionDefaults } from 'ol/interaction';
import { Select } from 'ol/interaction';
import { Tile as TileLayer, Vector as VectorLayer } from 'ol/layer';
import { Vector as VectorSource, OSM as OSMSource } from 'ol/source';
import { transformExtent, transform } from 'ol/proj';
import { SpatialCoverage } from '../../api/types';
import { PersistentComponent } from '../visus/PersistentComponent/PersistentComponent';
import Polygon from 'ol/geom/Polygon';
import Style from 'ol/style/Style';
import Stroke from 'ol/style/Stroke';
import Fill from 'ol/style/Fill';
import { click } from 'ol/events/condition';
import './GeoSpatialCoverageMap.css';
import { transformCoordinates, centralizeMapToExtent } from '../spatial-utils';
import 'ol/ol.css';

interface GeoSpatialCoverageMapProps {
  coverage: SpatialCoverage;
}

class GeoSpatialCoverageMap extends PersistentComponent<
  GeoSpatialCoverageMapProps
> {
  mapId = generateRandomId();
  mapRef: React.RefObject<HTMLDivElement>;
  containerRef: React.RefObject<HTMLDivElement>;
  popupContentRef: React.RefObject<HTMLDivElement>;

  constructor(props: GeoSpatialCoverageMapProps) {
    super(props);
    this.containerRef = React.createRef();
    this.popupContentRef = React.createRef();
    this.mapRef = React.createRef();
    this.state = {
      selectedCoordinates: undefined,
    };
  }

  createPolygons(element: SpatialCoverage) {
    // collect all the bounding boxes and find their
    // extent (outer bounding box)
    const polygons = [];

    let topLeft = element.ranges[0].range.coordinates[0];
    let bottomRight = element.ranges[0].range.coordinates[1];
    let minX = topLeft[0];
    let maxX = bottomRight[0];
    let minY = bottomRight[1];
    let maxY = topLeft[1];

    polygons.push([
      [topLeft[0], topLeft[1]],
      [topLeft[0], bottomRight[1]],
      [bottomRight[0], bottomRight[1]],
      [bottomRight[0], topLeft[1]],
      [topLeft[0], topLeft[1]],
    ]);

    for (let j = 1; j < element.ranges.length; j++) {
      topLeft = element.ranges[j].range.coordinates[0];
      bottomRight = element.ranges[j].range.coordinates[1];
      minX = Math.min(topLeft[0], minX);
      maxX = Math.max(bottomRight[0], maxX);
      minY = Math.min(bottomRight[1], minY);
      maxY = Math.max(topLeft[1], maxY);

      polygons.push([
        [topLeft[0], topLeft[1]],
        [topLeft[0], bottomRight[1]],
        [bottomRight[0], bottomRight[1]],
        [bottomRight[0], topLeft[1]],
        [topLeft[0], topLeft[1]],
      ]);
    }
    const extent = transformExtent(
      [minX, minY, maxX, maxY],
      'EPSG:4326',
      'EPSG:3857'
    );
    return { extent, polygons };
  }

  componentDidMount() {
    this.setupMap();
  }

  componentDidUpdate() {
    // Remove all children from the map div to force re-render
    const mapNode = this.mapRef.current;
    if (mapNode) {
      while (mapNode.firstChild) {
        if (mapNode.lastChild) {
          mapNode.removeChild(mapNode.lastChild);
        }
      }
      this.setupMap();
    }
  }

  setupMap() {
    const { polygons, extent } = this.createPolygons(this.props.coverage);

    const raster = new TileLayer({
      source: new OSMSource(),
    });

    const source = new VectorSource({ wrapX: false });

    // drawing bounding boxes
    for (let j = 0; j < polygons.length; j++) {
      const polygon = new Polygon([polygons[j]]);
      polygon.transform('EPSG:4326', 'EPSG:3857');
      const feature = new Feature(polygon);
      source.addFeature(feature);
    }

    const style = new Style({
      stroke: new Stroke({
        color: '#57068c',
        width: 3,
      }),
      fill: new Fill({
        color: '#ffffff',
      }),
    });

    const vector = new VectorLayer({
      source,
      style,
      opacity: 0.5,
    });

    // popup with bounding boxes
    const container = this.containerRef.current
      ? this.containerRef.current
      : undefined;

    const overlay = new Overlay({
      id: 'overlay',
      element: container,
      autoPan: true,
      autoPanAnimation: {
        duration: 250,
      },
    });

    const map = new Map({
      interactions: interactionDefaults({ mouseWheelZoom: false }),
      layers: [raster, vector],
      overlays: [overlay], // [overlays[index]],
      target: this.mapId, //'map-' + index,
      view: new View({
        projection: 'EPSG:3857',
        center: [0, 0],
        zoom: 0,
      }),
    });

    centralizeMapToExtent(map, extent);

    this.setupHoverPopUp(map);
  }

  setupHoverPopUp(map: Map) {
    const selectClick = new Select({
      condition: click,
    });

    map.addInteraction(selectClick);

    selectClick.on('select', evt => {
      const feature = map.forEachFeatureAtPixel(
        evt.mapBrowserEvent.pixel,
        feature => {
          return feature;
        }
      );
      if (feature) {
        const {
          topLeftLat,
          topLeftLon,
          bottomRightLat,
          bottomRightLon,
        } = transformCoordinates(feature);

        const topLeft = toStringHDMS([topLeftLon, topLeftLat]);
        const bottomRight = toStringHDMS([bottomRightLon, bottomRightLat]);

        const content = this.popupContentRef.current;
        if (content) {
          content.innerHTML =
            '<span>Top Left: </span><code>' +
            topLeft +
            '</code> </br>' +
            '<span>Bottom Right: </span><code>' +
            bottomRight +
            '</code>';
          map
            .getOverlayById('overlay')
            .setPosition(
              transform([topLeftLon, topLeftLat], 'EPSG:4326', 'EPSG:3857')
            );
        }
      } else {
        map.getOverlayById('overlay'); //.setPosition(undefined);
      }
    });
  }

  renderCoverageColumns(coverage: SpatialCoverage) {
    const { lat, lon, address, point, admin } = coverage;
    if (lat && lon) {
      return (
        <>
          <b>Latitude Column: </b>
          <span className="badge badge-pill badge-secondary mr-1">{lat}</span>
          &nbsp;|&nbsp;&nbsp;
          <b>Longitude Column: </b>
          <span className="badge badge-pill badge-secondary mr-1">{lon}</span>
        </>
      );
    }
    if (address) {
      return (
        <>
          <b>Address Column: </b>
          <span className="badge badge-pill badge-secondary mr-1">
            {address}
          </span>
        </>
      );
    }
    if (point) {
      return (
        <>
          <b>Point Column: </b>
          <span className="badge badge-pill badge-secondary mr-1">{point}</span>
        </>
      );
    }
    if (admin) {
      return (
        <>
          <b>Administrative Area Column: </b>
          <span className="badge badge-pill badge-secondary mr-1">{admin}</span>
        </>
      );
    }
    return (
      <>
        <b>Other Coverage: </b>
      </>
    );
  }

  render() {
    const style = { width: '100%', height: '400px' };
    return (
      <div style={{ display: 'block' }}>
        <div className="mb-2 mt-2">
          {this.renderCoverageColumns(this.props.coverage)}
        </div>
        <div id={this.mapId} ref={this.mapRef} className="map" style={style} />
        <span className="mb-3" style={{ fontSize: '0.9rem' }}>
          Left-click on bounding box to get more information.
        </span>
        <div ref={this.containerRef} className="ol-popup">
          <div ref={this.popupContentRef} />
        </div>
      </div>
    );
  }
}

export { GeoSpatialCoverageMap };
