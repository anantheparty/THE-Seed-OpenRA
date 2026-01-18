use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    MetricsCard = <View> {
        width: Fill,
        height: Fit,
        flow: Down,
        spacing: 8,
        padding: 16,
        margin: {bottom: 12},

        show_bg: true,
        draw_bg: {
            color: #252530

            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 6.0);
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

            label = <Label> {
                width: Fill,
                text: "Metric Name",
                draw_text: {
                    color: #888,
                    text_style: { font_size: 11.0 }
                }
            }

            trend = <Label> {
                text: "",
                draw_text: {
                    color: #00ff88,
                    text_style: { font_size: 14.0 }
                }
            }
        }

        value = <Label> {
            width: Fill,
            text: "0",
            draw_text: {
                color: #fff,
                text_style: { font_size: 24.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
            }
        }

        unit = <Label> {
            text: "unit",
            draw_text: {
                color: #666,
                text_style: { font_size: 10.0 }
            }
        }
    }

    // Compact version for smaller metrics
    MetricsCardCompact = <View> {
        width: Fill,
        height: Fit,
        flow: Down,
        spacing: 6,
        padding: 12,
        margin: {bottom: 10},

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

        label = <Label> {
            text: "Metric",
            draw_text: {
                color: #888,
                text_style: { font_size: 10.0 }
            }
        }

        <View> {
            width: Fill,
            height: Fit,
            flow: Right,
            align: {y: 0.5},
            spacing: 6,

            value = <Label> {
                width: Fit,
                text: "0",
                draw_text: {
                    color: #fff,
                    text_style: { font_size: 18.0 }
                }
            }

            unit = <Label> {
                text: "unit",
                draw_text: {
                    color: #666,
                    text_style: { font_size: 9.0 }
                }
            }
        }
    }
}
