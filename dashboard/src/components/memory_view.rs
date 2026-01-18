use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    MemoryEntryRow = <View> {
        width: Fill,
        height: Fit,
        padding: {left: 12, right: 12, top: 8, bottom: 8},
        flow: Right,
        spacing: 12,
        align: {y: 0.5},

        show_bg: true,
        draw_bg: {
            color: #252530

            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.box(0.0, 0.0, self.rect_size.x, self.rect_size.y, 3.0);
                sdf.fill(self.color);
                return sdf.result;
            }
        }

        key = <Label> {
            width: 200,
            text: "memory_key",
            draw_text: {
                color: #00ff88,
                text_style: { font_size: 11.0 }
            }
        }

        value = <Label> {
            width: Fill,
            text: "memory value...",
            draw_text: {
                color: #ddd,
                text_style: { font_size: 10.0 },
                wrap: Word
            }
        }

        timestamp = <Label> {
            width: Fit,
            text: "00:00:00",
            draw_text: {
                color: #666,
                text_style: { font_size: 9.0 }
            }
        }
    }

    MemoryStatsCard = <View> {
        width: Fill,
        height: Fit,
        padding: 16,
        flow: Down,
        spacing: 12,
        margin: {bottom: 16},

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

        <Label> {
            text: "Memory Statistics",
            draw_text: {
                color: #fff,
                text_style: { font_size: 14.0 }
            }
        }

        <View> {
            width: Fill,
            height: Fit,
            flow: Right,
            spacing: 24,

            <View> {
                width: Fit,
                height: Fit,
                flow: Down,
                spacing: 4,

                <Label> {
                    text: "Total Entries",
                    draw_text: {
                        color: #888,
                        text_style: { font_size: 10.0 }
                    }
                }

                total_entries = <Label> {
                    text: "0",
                    draw_text: {
                        color: #fff,
                        text_style: { font_size: 20.0 }
                    }
                }
            }

            <View> {
                width: Fit,
                height: Fit,
                flow: Down,
                spacing: 4,

                <Label> {
                    text: "Recent Queries",
                    draw_text: {
                        color: #888,
                        text_style: { font_size: 10.0 }
                    }
                }

                recent_queries = <Label> {
                    text: "0",
                    draw_text: {
                        color: #0af,
                        text_style: { font_size: 20.0 }
                    }
                }
            }

            <View> {
                width: Fit,
                height: Fit,
                flow: Down,
                spacing: 4,

                <Label> {
                    text: "New Additions",
                    draw_text: {
                        color: #888,
                        text_style: { font_size: 10.0 }
                    }
                }

                new_additions = <Label> {
                    text: "0",
                    draw_text: {
                        color: #0f0,
                        text_style: { font_size: 20.0 }
                    }
                }
            }
        }
    }

    MemoryView = <View> {
        width: Fill,
        height: Fill,
        flow: Down,
        spacing: 0,
        padding: 20,

        <ScrollView> {
            width: Fill,
            height: Fill,

            content = <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 12,

                stats_card = <MemoryStatsCard> {}

                <Label> {
                    text: "Recent Memory Entries",
                    draw_text: {
                        color: #fff,
                        text_style: { font_size: 14.0 }
                    }
                }

                entry_list = <View> {
                    width: Fill,
                    height: Fit,
                    flow: Down,
                    spacing: 8,

                    // Placeholder
                    <MemoryEntryRow> {
                        key = { text: "waiting..." }
                        value = { text: "No memory data yet" }
                        timestamp = { text: "00:00:00" }
                    }
                }
            }
        }
    }
}
