if(!Object.entries) {
  Object.entries = function(obj) {
    var ownProps = Object.keys(obj),
      i = ownProps.length,
      resArray = new Array(i); // preallocate the Array
    while(i--) {
      resArray[i] = [ownProps[i], obj[ownProps[i]]];
    }

    return resArray;
  };
}

function getCookie(name) {
  var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
  return r ? r[1] : undefined;
}

function getJSON(url='', args) {
  if(args) {
    args = '&' + encodeGetParams(args);
  } else {
    args = '';
  }
  return fetch(
    url + '?_xsrf=' + encodeURIComponent(getCookie('_xsrf')) + args,
    {
      credentials: 'same-origin',
      mode: 'same-origin'
    }
  ).then(function(response) {
    if(response.status != 200) {
      throw "Status " + response.status;
    }
    return response.json();
  });
}

function linkDataset(dataset_id) {
  dataset_id = escape(dataset_id);
  return '<a class="dataset" href="/dataset/' + dataset_id + '">' + dataset_id + '</a>';
}

function loadStatus() {
  getJSON('/status')
  .then(function(result) {
    var discoverers = document.getElementById('discoverers');
    discoverers.innerHTML = '';
    for(var i = 0; i < result.discoverers.length; ++i) {
      var elem = document.createElement('li');
      elem.innerHTML = result.discoverers[i][0] + " (" + result.discoverers[i][1] + ")";
      discoverers.appendChild(elem);
    }
    if(result.discoverers.length == 0) {
      var elem = document.createElement('li');
      elem.innerHTML = "No discoverer connected";
      discoverers.appendChild(elem);
    }

    var ingesters = document.getElementById('ingesters');
    ingesters.innerHTML = '';
    for(var i = 0; i < result.ingesters.length; ++i) {
      var elem = document.createElement('li');
      elem.innerHTML = result.ingesters[i][0] + " (" + result.ingesters[i][1] + ")";
      ingesters.appendChild(elem);
    }
    if(result.ingesters.length == 0) {
      var elem = document.createElement('li');
      elem.innerHTML = "No ingester connected";
      ingesters.appendChild(elem);
    }

    var recent_discoveries = document.getElementById('recent_discoveries');
    recent_discoveries.innerHTML = '';
    for(var i = 0; i < result.recent_discoveries.length; ++i) {
      var elem = document.createElement('li');
      elem.innerHTML =
        linkDataset(result.recent_discoveries[i][0]) +
        ' (' + result.recent_discoveries[i][1] + ')';
      recent_discoveries.appendChild(elem);
    }
    if(result.recent_discoveries.length == 0) {
      var elem = document.createElement('li');
      elem.innerHTML = "No recent discoveries";
      recent_discoveries.appendChild(elem);
    }

    var storage = document.getElementById('storage');
    storage.innerHTML = '';
    var entries = Object.entries(result.storage);
    for(var i = 0; i < entries.length; ++i) {
      var elem = document.createElement('li');
      if(entries[i][1] == null) {
        elem.innerHTML = entries[i][0] + ' (allocated)';
      } else {
        elem.innerHTML = '<span style="font-family: monospace;">' + entries[i][0] + '</span> ' + linkDataset(entries[i][1][0]) + ' ' + entries[i][1][1].join(', ');
      }
      storage.appendChild(elem);
    }
    if(entries.length == 0) {
      var elem = document.createElement('li');
      elem.innerHTML = "No dataset in local storage";
      storage.appendChild(elem);
    }
  });
}

loadStatus();
setInterval(loadStatus, 2000);
