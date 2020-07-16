/* globals _, edx */

(function($, _) {  // eslint-disable-line wrap-iife
    'use strict';
    var CanvasIntegration = (function() {

      function tableify(data) {
        var keys = Object.keys(data[0])
        var keysLookup = {}
        keys.forEach(function(key, i) {
          keysLookup[key] = i
        })

        var rows = data.map(function(dict) {
          var cols = keys.map(function(key) { return "<td>" + dict[key] + "</td>" }).join("")
          return "<tr>" + cols + "</tr>"
        }).join("")
        var headerRow = "<tr>" + keys.map(function(key) {
          return "<th>" + key + "</th>";
        }).join("") + "</tr>"
        return "<table>" + headerRow + rows + "</table>"
      }

      function InstructorDashboardCanvasIntegration($section) {
        this.$section = $section;
        this.$section.data('wrapper', this);
        var $results = this.$section.find("#results");
        var $errors = this.$section.find("#errors");

        var showErrors = function(error) {
          $errors.html("Error: <pre>" + JSON.stringify(error, null, 4) + "</pre>")
        }
        var showResults = function(title, asTable) {
          return function(data) {
            var results = asTable ? tableify(data) : (
              "<pre>" + JSON.stringify(data, null, 4) + "</pre>"
            );

            $results.html(title + ": " + results)
          }
        }

        this.$list_enrollments_btn = this.$section.find(
          "input[name='list-canvas-enrollments']"
        )
        this.$merge_canvas_enrollments_btn = this.$section.find(
          "input[name='merge-canvas-enrollments']"
        );
        this.$overload_canvas_enrollments_btn = this.$section.find(
          "input[name='overload-canvas-enrollments']"
        );
        var mergeHandler = function (event) {
          var $el = $(event.target);
          var url = $el.data('endpoint');
          $results.html("");
          return $.ajax({
            type: 'POST',
            dataType: 'json',
            url: url,
            data: {unenroll_current: $el.data('unenroll-current')},
          }).then(showResults("Status", false)).fail(showErrors);
        };
        this.$merge_canvas_enrollments_btn.click(mergeHandler);
        this.$overload_canvas_enrollments_btn.click(mergeHandler);
        this.$list_enrollments_btn.click(function(event) {
          var $el = $(event.target);
          var url = $el.data('endpoint');
          $results.html("");
          return $.ajax({
            type: 'GET',
            dataType: 'json',
            url: url,
          }).then(showResults("Enrollments on Canvas", true)).fail(showErrors)
        });
      }
      InstructorDashboardCanvasIntegration.prototype.onClickTitle = function() {};

      return InstructorDashboardCanvasIntegration
    })();

    _.defaults(window, {
        InstructorDashboard: {}
    });

    _.defaults(window.InstructorDashboard, {
        sections: {}
    });

    _.defaults(window.InstructorDashboard.sections, {
        CanvasIntegration: CanvasIntegration
    });
}).call(this, $, _);
