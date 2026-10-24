/* Small fallbacks for older embedded mobile browsers (Baidu/Quark webviews). */
(function (window, document) {
  'use strict';
  var element = window.Element && window.Element.prototype;
  if (element && !element.matches) element.matches = element.msMatchesSelector || element.webkitMatchesSelector;
  if (element && !element.closest) element.closest = function (selector) { var node = this; while (node && node.nodeType === 1) { if (node.matches && node.matches(selector)) return node; node = node.parentElement; } return null; };
  if (element && !element.append) element.append = function () { for (var i = 0; i < arguments.length; i++) this.appendChild(arguments[i] && arguments[i].nodeType ? arguments[i] : document.createTextNode(String(arguments[i]))); };
  if (element && !element.replaceChildren) element.replaceChildren = function () { while (this.firstChild) this.removeChild(this.firstChild); element.append.apply(this, arguments); };
  if (!window.URLSearchParams) {
    window.URLSearchParams = function (input) {
      this.values = Object.create(null);
      if (input && typeof input === 'object' && typeof input !== 'string') {
        for (var key in input) if (Object.prototype.hasOwnProperty.call(input, key)) this.values[key] = String(input[key]);
      } else {
        String(input || '').replace(/^\?/, '').split('&').forEach(function (pair) {
          if (!pair) return;
          var parts = pair.split('='), key, value;
          try { key = decodeURIComponent(parts.shift().replace(/\+/g, ' ')); value = decodeURIComponent(parts.join('=').replace(/\+/g, ' ')); } catch (_) { return; }
          if (key) this.values[key] = value;
        }, this);
      }
    };
    window.URLSearchParams.prototype.get = function (key) { return Object.prototype.hasOwnProperty.call(this.values, key) ? this.values[key] : null; };
    window.URLSearchParams.prototype.set = function (key, value) { this.values[key] = String(value); };
    window.URLSearchParams.prototype.delete = function (key) { delete this.values[key]; };
    window.URLSearchParams.prototype.toString = function () { var values = this.values; return Object.keys(values).map(function (key) { return encodeURIComponent(key) + '=' + encodeURIComponent(values[key]); }).join('&'); };
  }
  if (!String.prototype.normalize) String.prototype.normalize = function () { return String(this); };
  if (!window.requestAnimationFrame) window.requestAnimationFrame = function (callback) { return window.setTimeout(callback, 16); };
})(window, document);
