use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    RightPanel = <View> {
        width: 280,
        height: Fill,
        flow: Down,
        spacing: 0,

        show_bg: true,
        draw_bg: {
            color: #1a1a26
        }

        // Header
        <View> {
            width: Fill,
            height: Fit,
            padding: 16,
            show_bg: true,
            draw_bg: { color: #252530 }

            <Label> {
                text: "REAL-TIME METRICS",
                draw_text: {
                    color: #888,
                    text_style: { font_size: 11.0 }
                }
            }
        }

        // Metrics Content
        <View> {
            width: Fill,
            height: Fill,
            flow: Down,
            spacing: 10,
            padding: 16,

            // Connection Status
            <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 6,
                padding: 12,
                show_bg: true,
                draw_bg: {
                    color: #252530
                    fn pixel(self) -> vec4 {
                        let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                        sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 4.0);
                        sdf.fill(self.color);
                        return sdf.result;
                    }
                }

                <Label> {
                    text: "CONNECTION",
                    draw_text: {
                        color: #666,
                        text_style: { font_size: 9.0 }
                    }
                }

                status = <Label> {
                    text: "Disconnected",
                    draw_text: {
                        color: #f00,
                        text_style: { font_size: 13.0 }
                    }
                }
            }

            // Placeholder metrics cards
            <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 6,
                padding: 12,
                show_bg: true,
                draw_bg: {
                    color: #252530
                    fn pixel(self) -> vec4 {
                        let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                        sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 4.0);
                        sdf.fill(self.color);
                        return sdf.result;
                    }
                }

                <Label> {
                    text: "AGENT METRICS",
                    draw_text: {
                        color: #666,
                        text_style: { font_size: 9.0 }
                    }
                }

                metrics_placeholder = <Label> {
                    text: "Waiting for data...",
                    draw_text: {
                        color: #888,
                        text_style: { font_size: 11.0 }
                    }
                }
            }
        }
    }
}
