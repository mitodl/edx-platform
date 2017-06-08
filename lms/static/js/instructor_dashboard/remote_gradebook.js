/* globals _ */

(function($, _) {
    'use strict';
    var RemoteGradebook;

    RemoteGradebook = (function() {
        function InstructorDashboardRemoteGradebook($section) {
            var remoteGradebookObj = this;
            this.$section = $section;
            this.$section.data('wrapper', this);
            this.$results = this.$section.find("#results");
            this.$errors = this.$section.find("#errors");
            this.$loading = this.$section.find(".loading");
            this.$assignment_name_input = this.$section.find("input[name='assignment-name']");
            this.$list_remote_assign_btn = this.$section.find("input[name='list-remote-assignments']");
            this.$list_remote_enrolled_students_btn = this.$section.find("input[name='list-remote-enrolled-students']");
            this.$list_course_assignments_btn = this.$section.find("input[name='list-course-assignments']");
            this.$display_assignment_grades_btn = this.$section.find("input[name='display-assignment-grades']");
            this.$export_assignment_grades_to_rg_btn = this.$section.find("input[name='export-assignment-grades-to-rg']");
            this.$export_assignment_grades_csv_btn = this.$section.find("input[name='export-assignment-grades-csv']");

            this.datatableTemplate = _.template($('#html-datatable-tpl').text());

            this.showResults = function(resultHTML) {
                remoteGradebookObj.$results.html(resultHTML);
                remoteGradebookObj.$errors.empty();
            };

            this.showErrors = function(errorHTML) {
                remoteGradebookObj.$results.empty();
                remoteGradebookObj.$errors.html(errorHTML);
            };

            function addDatatableClickHandler($el, createRequestData) {
                $el.click(function() {
                    var url = $el.data('endpoint');
                    remoteGradebookObj.$loading.removeClass('hidden');
                    return $.ajax({
                        type: 'POST',
                        dataType: 'json',
                        url: url,
                        data: _.isFunction(createRequestData) ? createRequestData() : {},
                    })
                    .done(function(data) {
                        if (_.isEmpty(data)) {
                            remoteGradebookObj.showErrors('No results.');
                        } else if (_.isEmpty(data.errors)) {
                            remoteGradebookObj.showResults(remoteGradebookObj.datatableTemplate(data.datatable));
                        } else {
                            remoteGradebookObj.showErrors(data.errors);
                        }
                    })
                    .fail(function() {
                        remoteGradebookObj.showErrors('Request failed.');
                    })
                    .always(function() {
                        remoteGradebookObj.$loading.addClass('hidden');
                    });
                });
            }

            function getAssignmentNameForRequest() {
                return {
                    assignment_name: remoteGradebookObj.$assignment_name_input.val()
                };
            }

            addDatatableClickHandler(this.$list_remote_assign_btn);
            addDatatableClickHandler(this.$list_remote_enrolled_students_btn);
            addDatatableClickHandler(this.$list_course_assignments_btn);
            addDatatableClickHandler(this.$display_assignment_grades_btn, getAssignmentNameForRequest);
            addDatatableClickHandler(this.$export_assignment_grades_to_rg_btn, getAssignmentNameForRequest);

            this.$export_assignment_grades_csv_btn.click(function() {
                var assignmentName = encodeURIComponent(remoteGradebookObj.$assignment_name_input.val());
                if (assignmentName) {
                    location.href = remoteGradebookObj.$export_assignment_grades_csv_btn.data('endpoint')
                        + '?assignment_name='
                        + assignmentName;
                } else {
                    remoteGradebookObj.showErrors('Assignment name must be specified.');
                }
            });
        }

        InstructorDashboardRemoteGradebook.prototype.onClickTitle = function() {
            this.$errors.empty();
            this.$results.empty();
        };

        return InstructorDashboardRemoteGradebook;
    }());

    _.defaults(window, {
        InstructorDashboard: {}
    });

    _.defaults(window.InstructorDashboard, {
        sections: {}
    });

    _.defaults(window.InstructorDashboard.sections, {
        RemoteGradebook: RemoteGradebook
    });

}).call(this, $, _);
