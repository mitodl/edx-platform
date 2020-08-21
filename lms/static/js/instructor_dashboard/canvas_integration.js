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
        var $loading = this.$section.find("#loading");
        var $listEnrollmentsBtn = this.$section.find(
          "input[name='list-canvas-enrollments']"
        );
        var $mergeCanvasEnrollmentsBtn = this.$section.find(
          "input[name='merge-canvas-enrollments']"
        );
        var $overloadCanvasEnrollmentsBtn = this.$section.find(
          "input[name='overload-canvas-enrollments']"
        );
        var $loadCanvasAssignmentsBtn = this.$section.find(
          "input[name='load-canvas-assignments']"
        );
        var $listCanvasGradesBtn = this.$section.find("input[name='list-canvas-grades']");
        var $canvasAssignSection = this.$section.find("#canvas-assignment-section");
        var $pushAllEdxGradesBtn = this.$section.find("input[name='push-all-edx-grades']");
        var $assignmentInput = this.$section.find("select[name='assignment-id']");

        var setLoading = function() {
          $errors.html("")
          $results.html("")
          $loading.show()
        }
        var stopLoading = function () {
          $loading.hide()
        }
        var showErrors = function(error) {
          $loading.hide()
          $results.html("")
          $errors.html("Error: <pre>" + JSON.stringify(error, null, 4) + "</pre>")
        }
        var showResults = function(title, asTable) {
          return function(data) {
            var results = asTable ? tableify(data) : (
              "<pre>" + JSON.stringify(data, null, 4) + "</pre>"
            );
            $loading.hide()
            $errors.html("")
            $results.html(title + ": " + results)
          }
        }

        var loadEdxAssignmentOptions = function($el) {
          var url = $el.data('endpoint');
          return $.ajax({
            type: 'POST',
            dataType: 'json',
            url: url
          }).done(function(resp) {
            var data = resp.data
            // If the data returned is a list of lists, convert it to an object/dictionary
            if (_.every(data, _.isArray)) {
              data = _.object(data)
            }
            var isArray = _.isArray(data)
            _.map(data, function(optionLabel, optionValue) {
              // If an array was returned, use each value as both the option value and label
              optionValue = isArray ? optionLabel : optionValue
              $el.append(
                $('<option></option>')
                  .attr('value', optionValue)
                  .text(optionLabel)
              );
            });
          }).fail(function() {
            $("<span></span>").addClass('errors').text("Failed to load assignments.").insertAfter($el);
          })
        }

        var mergeHandler = function (event) {
          var $el = $(event.target);
          var url = $el.data('endpoint');
          setLoading();
          return $.ajax({
            type: 'POST',
            dataType: 'json',
            url: url,
            data: {unenroll_current: $el.data('unenroll-current')},
          }).then(
            showResults("Status", false)
          ).fail(
            showErrors
          ).always(
            stopLoading
          );
        };

        $mergeCanvasEnrollmentsBtn.click(mergeHandler);
        $overloadCanvasEnrollmentsBtn.click(mergeHandler);
        $listEnrollmentsBtn.click(function(event) {
          var $el = $(event.target);
          var url = $el.data('endpoint');
          setLoading();
          return $.ajax({
            type: 'GET',
            dataType: 'json',
            url: url,
          }).then(
            showResults("Enrollments on Canvas", true)
          ).fail(
            showErrors
          ).always(
            stopLoading
          );
        });
        $loadCanvasAssignmentsBtn.click(function(event) {
          var $el = $(event.target);
          var url = $el.data('endpoint');
          setLoading();
          $canvasAssignSection.hide()
          return $.ajax({
            type: 'GET',
            dataType: 'json',
            url: url,
          }).then(function (assignments) {
            $canvasAssignSection.show()
            $assignmentInput.empty()
            assignments.forEach(function(assignment) {
              var $option = $("<option />")
              $option.val(assignment.id).text(assignment.name)
              $assignmentInput.append($option)
            })
          }).fail(
            showErrors
          ).always(
            stopLoading
          );
        })
        $listCanvasGradesBtn.click(function(event) {
          var assignmentId = parseInt($assignmentInput.val())
          var $el = $(event.target);
          var url = $el.data('endpoint');
          setLoading();
          return $.ajax({
            type: 'GET',
            dataType: 'json',
            url: url,
            data: {
              "assignment_id": assignmentId,
            }
          }).then(
            showResults("Grades on Canvas", false)
          ).fail(
            showErrors
          ).always(
            stopLoading
          );
        })
        $pushAllEdxGradesBtn.click(function(event) {
          var $el = $(event.target);
          var url = $el.data('endpoint');
          setLoading();
          return $.ajax({
            type: 'POST',
            dataType: 'json',
            url: url
          }).then(
            showResults("Grade Update Results", false)
          ).fail(
            showErrors
          ).always(
            stopLoading
          );
        })

        // Immediately load assignments configured on the edX side
        loadEdxAssignmentOptions($edxAssignmentSelect);
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
