use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    TimelineEntry = <View> {
        width: Fill,
        height: Fit,
        padding: {left: 16, right: 16, top: 10, bottom: 10},
        flow: Down,
        spacing: 6,

        show_bg: true,
        draw_bg: {
            color: #202030

            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 4.0);
                sdf.fill(self.color);
                sdf.stroke(#333348, 1.0);
                return sdf.result;
            }
        }

        <View> {
            width: Fill,
            height: Fit,
            flow: Right,
            align: {y: 0.5},
            spacing: 10,

            timestamp = <Label> {
                width: Fit,
                text: "00:00:00.000",
                draw_text: {
                    color: #666,
                    text_style: { font_size: 9.0 }
                }
            }

            <View> { width: Fill }

            arrow = <Label> {
                text: "→",
                draw_text: {
                    color: #00ff88,
                    text_style: { font_size: 14.0 }
                }
            }
        }

        <View> {
            width: Fill,
            height: Fit,
            flow: Right,
            spacing: 12,
            align: {y: 0.5},

            from_state = <Label> {
                width: Fit,
                text: "STATE_A",
                draw_text: {
                    color: #ea0,
                    text_style: { font_size: 11.0 }
                }
            }

            <Label> {
                text: "→",
                draw_text: {
                    color: #666,
                    text_style: { font_size: 12.0 }
                }
            }

            to_state = <Label> {
                width: Fit,
                text: "STATE_B",
                draw_text: {
                    color: #0f0,
                    text_style: { font_size: 11.0 }
                }
            }
        }

        details = <Label> {
            width: Fill,
            text: "Transition details...",
            draw_text: {
                color: #888,
                text_style: { font_size: 10.0 },
                wrap: Word
            }
        }
    }

    HistoryTimeline = <View> {
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
            show_bg: true,
            draw_bg: { color: #252530 }

            <Label> {
                text: "FSM Transition History",
                draw_text: {
                    color: #fff,
                    text_style: { font_size: 13.0 }
                }
            }
        }

        // Timeline content
        <ScrollView> {
            width: Fill,
            height: Fill,

            timeline_list = <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 10,
                padding: 12,

                // Placeholder entry
                <TimelineEntry> {
                    timestamp = { text: "00:00:00.000" }
                    from_state = { text: "Idle" }
                    to_state = { text: "Planning" }
                    details = { text: "No transition history yet" }
                }
            }
        }
    }

    ActionDetailView = <View> {
        width: Fill,
        height: Fit,
        flow: Down,
        spacing: 12,
        padding: 16,

        show_bg: true,
        draw_bg: {
            color: #252530

            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 6.0);
                sdf.fill(self.color);
                return sdf.result;
            }
        }

        <Label> {
            text: "Current Action",
            draw_text: {
                color: #888,
                text_style: { font_size: 11.0 }
            }
        }

        action_name = <Label> {
            width: Fill,
            text: "-",
            draw_text: {
                color: #fff,
                text_style: { font_size: 16.0 }
            }
        }

        <View> { height: 8 }

        <Label> {
            text: "Status",
            draw_text: {
                color: #888,
                text_style: { font_size: 10.0 }
            }
        }

        action_status = <Label> {
            text: "Idle",
            draw_text: {
                color: #0af,
                text_style: { font_size: 12.0 }
            }
        }

        <View> { height: 8 }

        <Label> {
            text: "Details",
            draw_text: {
                color: #888,
                text_style: { font_size: 10.0 }
            }
        }

        action_details = <Label> {
            width: Fill,
            text: "No action details available",
            draw_text: {
                color: #ddd,
                text_style: { font_size: 11.0 },
                wrap: Word
            }
        }
    }
}
