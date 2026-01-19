use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    LeftPanel = <View> {
        width: 250,
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
                text: "FSM STATE",
                draw_text: {
                    color: #888,
                    text_style: { font_size: 11.0 }
                }
            }
        }

        // Content
        <View> {
            width: Fill,
            height: Fill,
            flow: Down,
            spacing: 12,
            padding: 16,

            // Current State Card
            <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 8,
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
                    text: "Current State",
                    draw_text: {
                        color: #666,
                        text_style: { font_size: 10.0 }
                    }
                }

                current_state = <Label> {
                    width: Fill,
                    text: "-",
                    draw_text: {
                        color: #00ff88,
                        text_style: { font_size: 16.0 }
                    }
                }
            }

            // Goal Card
            <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 8,
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
                    text: "Current Goal",
                    draw_text: {
                        color: #666,
                        text_style: { font_size: 10.0 }
                    }
                }

                goal = <Label> {
                    width: Fill,
                    text: "-",
                    draw_text: {
                        color: #fff,
                        text_style: { font_size: 12.0 },
                        wrap: Word
                    }
                }
            }

            // Step Info Card
            <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 8,
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
                    text: "Progress",
                    draw_text: {
                        color: #666,
                        text_style: { font_size: 10.0 }
                    }
                }

                step_info = <Label> {
                    width: Fill,
                    text: "0 / 0",
                    draw_text: {
                        color: #ea0,
                        text_style: { font_size: 14.0 }
                    }
                }
            }

            // Plan Card
            <ScrollView> {
                width: Fill,
                height: Fill,

                <View> {
                    width: Fill,
                    height: Fit,
                    flow: Down,
                    spacing: 8,
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
                        text: "Plan",
                        draw_text: {
                            color: #666,
                            text_style: { font_size: 10.0 }
                        }
                    }

                    plan_list = <Label> {
                        width: Fill,
                        text: "No plan yet",
                        draw_text: {
                            color: #fff,
                            text_style: { font_size: 11.0, line_spacing: 1.6 },
                            wrap: Word
                        }
                    }
                }
            }
        }
    }
}
