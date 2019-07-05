/**
 * This is a component that is embedded within the ActiveVideoUploadListView and allows for
 * linking existing HLS videos to the course while bypassing the transcoding pipeline.
 */
define(["underscore", "gettext", "js/views/baseview"], function(_, gettext, BaseView) {
  var HLSVideoUploadView = BaseView.extend({
    missingFieldsText: gettext("Required data missing. Please fill the fields and try again."),
    defaultServerError: gettext("Server error, please refresh the page and try again."),
    hlsPlaybackError: gettext(
      "HLS playback is not enabled for this course or globally. Please enable this feature and try again."
    ),
    filenameText: gettext("Name of Video"),
    submitText: gettext("Submit"),
    urlText: gettext("URL"),
    formTitle: gettext("Link HLS Video"),
    tagName: "div",
    className: "hls-upload-container",
    events: {
      "click .btn-submit": "submitForm"
    },

    initialize: function(options) {
      this.postUrl = options.postUrl;
      this.onFileUploadDone = options.onFileUploadDone;
      this.isHlsPlaybackEnabled = options.isHlsPlaybackEnabled;
      this.template = this.loadTemplate("hls-video-upload");
    },

    render: function() {
      if (!this.$el.html()) {
        this.$el.html(
          this.template({
            urlText: this.urlText,
            submitText: this.submitText,
            filenameText: this.filenameText,
            hlsPlaybackError: this.hlsPlaybackError,
            isHlsPlaybackEnabled: this.isHlsPlaybackEnabled,
            toggleExpansionState: this.toggleExpansionState,
            formTitle: this.formTitle
          })
        );
        this.container = this.$el;
        this.filenameField = this.$el.find('input[name="filename"]');
        this.urlField = this.$el.find('input[name="hls_url"]');
        this.errorContainer = this.$el.find(".error");
        this.header = this.$el.find("h2");
        this.header.click(this.toggleExpansionState.bind(this));
      }

      return this;
    },
    submitForm: function(event) {
      event.preventDefault();

      let filename = this.filenameField.val();
      let hlsUrl = this.urlField.val();

      if (filename == false || hlsUrl == false) {
        this.errorContainer.html(this.missingFieldsText).show();
      } else {
        var self = this;
        $.ajax({
          url: this.postUrl,
          contentType: "application/json",
          data: JSON.stringify({
            filename: filename,
            hls_url: hlsUrl
          }),
          dataType: "json",
          type: "POST",
          global: false // Do not trigger global AJAX error handler
        })
          .done(function(responseData) {
            console.log(responseData);
            let collection = new Backbone.Collection(responseData.files);
            self.onFileUploadDone(collection);
            self.$el.find(".error").hide();
          })
          .fail(function(xhr) {
            var error = self.defaultServerError;
            if (xhr.responseJSON.error) {
              error = xhr.responseJSON.error;
            }

            self.$el.find(".error").html(error);
            self.$el.find(".error").show();
          });
      }
    },
    toggleExpansionState: function(event) {
      this.container.toggleClass("is-collapsed");
    }
  });

  return HLSVideoUploadView;
}); // end define();
