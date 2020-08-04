import MapBrowserEvent from 'ol/MapBrowserEvent';
import Geometry from 'ol/geom/Geometry';
import { FeatureLike } from 'ol/Feature';
import { Map } from 'ol';
import { Extent } from 'ol/extent';

//
// Following types are a temporary workaround to a bug in typings from the
// OpenLayers library (package @types/ol) This may be removed after upgrading
// this library to a newer version
//
export interface MyMapBrowserEvent extends MapBrowserEvent {
  pointerEvent: PointerEvent;
}

interface MyGeometry extends Geometry {
  getCoordinates(): number[][][];
}

export function transformCoordinates(feature: FeatureLike) {
  const geometry = feature.getGeometry() as MyGeometry;
  const transformedGeometry = geometry
    .clone()
    .transform('EPSG:3857', 'EPSG:4326') as MyGeometry;
  const coordinates = transformedGeometry.getCoordinates()[0];
  return {
    topLeftLat: coordinates[0][1],
    topLeftLon: coordinates[0][0],
    bottomRightLat: coordinates[2][1],
    bottomRightLon: coordinates[2][0],
  };
}

export function centralizeMapToExtent(map: Map, extent: Extent) {
  map.getView().fit(extent);
  map.updateSize();
}

export function centralizeMapToFeature(map: Map, feature: FeatureLike) {
  const extent = feature.getGeometry()?.getExtent();
  if (extent) {
    centralizeMapToExtent(map, extent);
  }
}

export function wrapLongitude(x: number) {
  if (-180 <= x && x <= 180) {
    return x;
  }
  console.log(`>  ${x} ...`);
  x += 180;
  x = x % 360;
  x = (x + 360) % 360; // second pass for negative remainders
  x -= 180;
  console.log(`<< ${x}`);
  return x;
}
