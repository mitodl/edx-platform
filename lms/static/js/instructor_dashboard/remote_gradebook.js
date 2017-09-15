/* globals _ */

(function($, _) {
    'use strict';
    var RemoteGradebook, PendingInstructorTasks, ReportDownloads, statusAjaxError;

    statusAjaxError = function() {
        return window.InstructorDashboard.util.statusAjaxError.apply(this, arguments);
    };

    PendingInstructorTasks = function() {
        return window.InstructorDashboard.util.PendingInstructorTasks;
    };

    ReportDownloads = function() {
        return window.InstructorDashboard.util.ReportDownloads;
    };

    RemoteGradebook = (function() {
        function InstructorDashboardRemoteGradebook($section) {
            var remoteGradebookObj = this;
            this.$section = $section;
            this.$section.data('wrapper', this);
            this.$results = this.$section.find("#results");
            this.$errors = this.$section.find("#errors");
            this.$loading = this.$section.find(".loading");
            this.$section_name_select = this.$section.find("#section-name");
            this.$assignment_name_select = this.$section.find("#assignment-name");
            this.$list_remote_enrolled_students_btn = this.$section.find("input[name='list-remote-enrolled-students']");
            this.$list_remote_students_in_section_btn = this.$section.find("input[name='list-remote-students-in-section']");
            this.$merge_enrolled_students_in_section_btn = this.$section.find("input[name='merge-enrolled-students-in-section']");
            this.$overload_enrolled_students_in_section_btn = this.$section.find("input[name='overload-enrolled-students-in-section']");
            this.$list_remote_assign_btn = this.$section.find("input[name='list-remote-assignments']");
            this.$list_course_assignments_btn = this.$section.find("input[name='list-course-assignments']");
            this.$display_assignment_grades_btn = this.$section.find("input[name='display-assignment-grades']");
            this.$export_assignment_grades_to_rg_btn = this.$section.find("input[name='export-assignment-grades-to-rg']");
            this.$export_assignment_grades_csv_btn = this.$section.find("input[name='export-assignment-grades-csv']");
            this.report_downloads = new (ReportDownloads())(this.$section);
            this.instructor_tasks = new (PendingInstructorTasks())(this.$section);
            this.clear_display();
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
                var $spinner =
                    $('<span></span>')
                        .addClass('icon fa fa-spinner fa-spin fa-large');
                var $spinnerContainer =
                    $('<span></span>')
                        .addClass('loading')
                        .append($spinner);
                if (text) {
                    $spinnerContainer.append(text);
                }
                return $spinnerContainer;
            }

            function loadSelectBoxOptions($el) {
                var url = $el.data('endpoint');
                var $spinner = createLoadingSpinner(gettext(' Loading options...'));
                $spinner
                    .css({'display': 'inline-block', 'padding-left': '10px'})
                    .insertAfter($el);

                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url
                })
                .done(function(respData) {
                    if (_.isEmpty(respData.errors)) {
                        $.each(respData.data, function (index, optionValue) {
                            $el.append(
                                $('<option></option>')
                                    .attr('value', optionValue)
                                    .text(optionValue)
                            );
                        });
                        $el.prop('disabled', false);
                    } else {
                        $('<span></span>')
                            .addClass('errors')
                            .append(respData.errors)
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
                    $spinner.remove();
                });
            }

            function fetchAndRenderDatatable($el, requestData) {
                var url = $el.data('endpoint');
                var $spinner = createLoadingSpinner();
                $spinner
                    .css('display', 'block')
                    .insertBefore(remoteGradebookObj.$errors);

                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    data: requestData || {}
                })
                .done(function(data) {
                    if (_.isEmpty(data)) {
                        remoteGradebookObj.showErrors(gettext('No results.'));
                    } else if (!_.isEmpty(data.errors)) {
                        remoteGradebookObj.showErrors(data.errors);
                    } else {
                        if (!_.isEmpty(data.datatable)) {
                            remoteGradebookObj.showResults(remoteGradebookObj.datatableTemplate(data.datatable));
                        } else {
                            remoteGradebookObj.showResults(data.message || '');
                        }
                    }
                })
                .fail(function() {
                    remoteGradebookObj.showErrors(gettext('Request failed.'));
                })
                .always(function() {
                    $spinner.remove();
                });
            }

            function datatableClickHandler(event) {
                var $el = $(event.target);
                var requestData = event.data && _.isFunction(event.data.requestDataFunc)
                    ? event.data.requestDataFunc($el)
                    : {};
                fetchAndRenderDatatable($el, requestData);
            }

            function getAssignmentNameForRequest() {
                return {
                    assignment_name: remoteGradebookObj.$assignment_name_select.val()
                };
            }

            function getSectionNameForRequest() {
                return {
                    section_name: remoteGradebookObj.$section_name_select.val()
                };
            }

            function getEnrollmentRequestData($el) {
                return _.extend(
                  {unenroll_current: $el.data('unenroll-current')},
                  getSectionNameForRequest()
                );
            }

            this.$list_remote_enrolled_students_btn.click(datatableClickHandler);
            this.$list_remote_students_in_section_btn.click(
                {requestDataFunc: getSectionNameForRequest},
                datatableClickHandler
            );
            this.$merge_enrolled_students_in_section_btn.click(
                {requestDataFunc: getEnrollmentRequestData},
                datatableClickHandler
            );
            this.$overload_enrolled_students_in_section_btn.click(
                {requestDataFunc: getEnrollmentRequestData},
                function(event) {
                    var $el = $(event.target);
                    var url = $el.data('enrolled-users-endpoint');
                    $.ajax({
                        type: 'POST',
                        dataType: 'json',
                        url: url
                    })
                    .done(function(data) {
                        var shouldOverload = true,
                            warning;
                        if (data.count > 0) {
                            warning = gettext('WARNING: This will unenroll non-staff users from the course.\n\n') +
                                gettext('Users ') + '(' + data.count + '): \n' +
                                data.users.join(', ');
                            if (data.count > data.users.length) {
                                warning += ', ...';
                            }
                            // Using window.confirm because the instructor dashboard is apparently not
                            // set up to use RequireJS. There are some custom confirmation components in
                            // the codebase (e.g.: common/static/common/js/components/utils/view_utils.js),
                            // but they're only usable via RequireJS.
                            shouldOverload = window.confirm(warning);
                        }
                        if (shouldOverload) {
                            datatableClickHandler(event);
                        }
                    });
                }
            );
            this.$list_remote_assign_btn.click(datatableClickHandler);
            this.$list_course_assignments_btn.click(datatableClickHandler);
            this.$display_assignment_grades_btn.click(
                {requestDataFunc: getAssignmentNameForRequest},
                datatableClickHandler
            );
            this.$export_assignment_grades_to_rg_btn.click(
                function() {
                var assignmentName = encodeURIComponent(remoteGradebookObj.$assignment_name_select.val());
                if (assignmentName) {
                   var url = remoteGradebookObj.$export_assignment_grades_to_rg_btn.data('endpoint')
                        + '?assignment_name='
                        + assignmentName;
                   remoteGradebookObj.clear_display();
                   return $.ajax({
                        type: 'GET',
                        dataType: 'json',
                        url: url,
                        error: function() {
                            remoteGradebookObj.showErrors(
                                gettext('Error posting grades to remote grade book. Please try again.')
                            );
                            remoteGradebookObj.setTaskErrorVisibility(true);
                        },
                        success: function(data) {
                            remoteGradebookObj.showResults(data.status);
                            remoteGradebookObj.setTaskMessageVisibility(true);
                        }
                   });
                } else {
                    remoteGradebookObj.showErrors(gettext('Assignment name must be specified.'));
                }
            });

            this.$export_assignment_grades_csv_btn.click(function() {
                var assignmentName = encodeURIComponent(remoteGradebookObj.$assignment_name_select.val());
                if (assignmentName) {
                   var url = remoteGradebookObj.$export_assignment_grades_csv_btn.data('endpoint')
                        + '?assignment_name='
                        + assignmentName;
                   remoteGradebookObj.clear_display();
                   return $.ajax({
                       type: 'GET',
                       dataType: 'json',
                       url: url,
                       error: function() {
                           remoteGradebookObj.showErrors(
                                gettext('Error generating grades. Please try again.')
                           );
                           remoteGradebookObj.setTaskErrorVisibility(true);
                       },
                       success: function(data) {
                           remoteGradebookObj.showResults(data.status);
                           remoteGradebookObj.setTaskMessageVisibility(true);
                       }
                   });
                } else {
                    remoteGradebookObj.showErrors(gettext('Assignment name must be specified.'));
                }
            });

            loadSelectBoxOptions(remoteGradebookObj.$section_name_select);
            loadSelectBoxOptions(remoteGradebookObj.$assignment_name_select);
        }

        InstructorDashboardRemoteGradebook.prototype.onClickTitle = function() {
            this.clear_display();
            this.instructor_tasks.task_poller.start();
            return this.report_downloads.downloads_poller.start();
        };

        InstructorDashboardRemoteGradebook.prototype.onExit = function() {
            this.instructor_tasks.task_poller.stop();
            return this.report_downloads.downloads_poller.stop();
        };

        InstructorDashboardRemoteGradebook.prototype.clear_display = function() {
            this.$errors.empty();
            this.$results.empty();
            this.setTaskMessageVisibility(false);
            this.setTaskErrorVisibility(false);
        };

        InstructorDashboardRemoteGradebook.prototype.setTaskMessageVisibility = function(visible) {
            $('.msg-confirm').css({display: visible ? 'block' : 'none'});
        };

        InstructorDashboardRemoteGradebook.prototype.setTaskErrorVisibility = function(visible) {
            $('.msg-error').css({display: visible ? 'block' : 'none'});
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
