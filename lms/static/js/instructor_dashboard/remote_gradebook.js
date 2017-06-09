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
            this.$section_name_select = this.$section.find("#section-name");
            this.$assignment_name_input = this.$section.find("#assignment-name");
            this.$list_remote_enrolled_students_btn = this.$section.find("input[name='list-remote-enrolled-students']");
            this.$list_remote_students_in_section_btn = this.$section.find("input[name='list-remote-students-in-section']");
            this.$list_remote_assign_btn = this.$section.find("input[name='list-remote-assignments']");
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

            function createLoadingSpinner(text) {
                var spinner =
                    $('<span></span>')
                        .addClass('icon fa fa-spinner fa-spin fa-large');
                var spinnerContainer =
                    $('<span></span>')
                        .addClass('loading')
                        .append(spinner);
                if (text) {
                    spinnerContainer.append(text);
                }
                return spinnerContainer;
            }

            function loadSectionNameOptions() {
                var $el = remoteGradebookObj.$section_name_select;
                var url = $el.data('endpoint');
                var spinner = createLoadingSpinner(gettext(' Loading section names...'));
                spinner
                    .css({'display': 'inline-block', 'padding-left': '10px'})
                    .insertAfter($el);

                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url
                })
                .done(function(data) {
                    if (_.isEmpty(data.errors)) {
                        $.each(data.sections, function (index, section) {
                            $el.append(
                                $('<option></option>')
                                    .attr('value', section)
                                    .text(section)
                            );
                        });
                        $el.prop('disabled', false);
                    } else {
                        $('<span></span>')
                            .addClass('errors')
                            .append(data.errors)
                            .insertAfter($el);
                    }
                })
                .fail(function() {
                    $('<span></span>')
                        .addClass('errors')
                        .append(gettext('Request failed.'))
                        .insertAfter($el);
                })
                .always(function() {
                    spinner.remove();
                });
            }

            function addDatatableClickHandler($el, createRequestData) {
                $el.click(function() {
                    var url = $el.data('endpoint');
                    var spinner = createLoadingSpinner();
                    spinner
                        .css('display', 'block')
                        .insertBefore(remoteGradebookObj.$errors);

                    return $.ajax({
                        type: 'POST',
                        dataType: 'json',
                        url: url,
                        data: _.isFunction(createRequestData) ? createRequestData() : {}
                    })
                    .done(function(data) {
                        if (_.isEmpty(data)) {
                            remoteGradebookObj.showErrors(gettext('No results.'));
                        } else if (_.isEmpty(data.errors)) {
                            remoteGradebookObj.showResults(remoteGradebookObj.datatableTemplate(data.datatable));
                        } else {
                            remoteGradebookObj.showErrors(data.errors);
                        }
                    })
                    .fail(function() {
                        remoteGradebookObj.showErrors(gettext('Request failed.'));
                    })
                    .always(function() {
                        spinner.remove();
                    });
                });
            }

            function getAssignmentNameForRequest() {
                return {
                    assignment_name: remoteGradebookObj.$assignment_name_input.val()
                };
            }

            function getSectionNameForRequest() {
                return {
                    section_name: remoteGradebookObj.$section_name_select.val()
                };
            }

            addDatatableClickHandler(this.$list_remote_enrolled_students_btn);
            addDatatableClickHandler(this.$list_remote_students_in_section_btn, getSectionNameForRequest);
            addDatatableClickHandler(this.$list_remote_assign_btn);
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
                    remoteGradebookObj.showErrors(gettext('Assignment name must be specified.'));
                }
            });

            loadSectionNameOptions();
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
