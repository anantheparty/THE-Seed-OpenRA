use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    LogEntry = <View> {
        width: Fill,
        height: Fit,
        padding: {left: 12, right: 12, top: 6, bottom: 6},
        flow: Right,
        spacing: 12,
        align: {y: 0.5},

        timestamp = <Label> {
            width: Fit,
            text: "00:00:00",
            draw_text: {
                color: #666,
                text_style: { font_size: 10.0 }
            }
        }

        level = <Label> {
            width: 50,
            text: "INFO",
            draw_text: {
                color: #0af,
                text_style: { font_size: 10.0 }
            }
        }

        message = <Label> {
            width: Fill,
            text: "Log message here...",
            draw_text: {
                color: #ddd,
                text_style: { font_size: 11.0 },
                wrap: Word
            }
        }
    }

    LogViewer = <View> {
        width: Fill,
        height: Fill,
        flow: Down,
        spacing: 0,

        show_bg: true,
        draw_bg: { color: #1a1a26 }

        // Header
        <View> {
            width: Fill,
            height: Fit,
            padding: 12,
            flow: Right,
            spacing: 10,
            align: {y: 0.5},
            show_bg: true,
            draw_bg: { color: #252530 }

            <Label> {
                text: "Logs",
                draw_text: {
                    color: #fff,
                    text_style: { font_size: 13.0 }
                }
            }

            <View> { width: Fill }

            // Filter buttons (placeholder)
            <Label> {
                text: "Filter: ALL",
                draw_text: {
                    color: #888,
                    text_style: { font_size: 10.0 }
                }
            }
        }

        // Log content area
        <ScrollView> {
            width: Fill,
            height: Fill,

            log_list = <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 0,

                // Placeholder logs - will be dynamically added
                <LogEntry> {
                    timestamp = { text: "00:00:00" }
                    level = { text: "INFO", draw_text: { color: #0af } }
                    message = { text: "Waiting for logs..." }
                }
            }
        }
    }
}
