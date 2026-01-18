use makepad_widgets::*;

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;
    use crate::components::metrics_card::*;

    // Tab button style
    TabButton = <Button> {
        width: Fit,
        height: Fit,
        padding: {left: 20, right: 20, top: 10, bottom: 10},

        draw_bg: {
            color: #2a2a38
            color_hover: #252530

            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);

                sdf.box(0.0, 0.0, self.rect_size.x, self.rect_size.y, 0.0);
                sdf.fill(mix(self.color, self.color_hover, self.hover));

                return sdf.result;
            }
        }

        draw_text: {
            color: #888
            text_style: { font_size: 11.0 }
        }
    }

    TabButtonActive = <TabButton> {
        draw_bg: {
            color: #252530

            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);

                sdf.box(0.0, 0.0, self.rect_size.x, self.rect_size.y, 0.0);
                sdf.fill(self.color);

                // Bottom border
                sdf.move_to(0.0, self.rect_size.y - 1.0);
                sdf.line_to(self.rect_size.x, self.rect_size.y - 1.0);
                sdf.stroke(#00ff88, 2.0);

                return sdf.result;
            }
        }

        draw_text: {
            color: #fff
        }
    }

    // Tab content panels
    AgentBenchmarkTab = <View> {
        width: Fill,
        height: Fill,
        padding: 20,
        flow: Down,
        spacing: 0,

        <ScrollView> {
            width: Fill,
            height: Fill,

            content = <View> {
                width: Fill,
                height: Fit,
                flow: Down,
                spacing: 16,

                // Header
                <Label> {
                    text: "Agent Performance Metrics",
                    draw_text: {
                        color: #fff,
                        text_style: { font_size: 18.0 }
                    }
                }

                // Primary metrics (2 columns)
                <View> {
                    width: Fill,
                    height: Fit,
                    flow: Right,
                    spacing: 16,

                    // Left column
                    <View> {
                        width: Fill,
                        height: Fit,
                        flow: Down,

                        tokens_card = <MetricsCard> {
                            label = { text: "Tokens / Min" }
                            value = { text: "0" }
                            unit = { text: "tokens/min" }
                            trend = { text: "" }
                        }

                        llm_calls_card = <MetricsCard> {
                            label = { text: "LLM Calls / Min" }
                            value = { text: "0" }
                            unit = { text: "calls/min" }
                            trend = { text: "" }
                        }
                    }

                    // Right column
                    <View> {
                        width: Fill,
                        height: Fit,
                        flow: Down,

                        tasks_card = <MetricsCard> {
                            label = { text: "Active Tasks" }
                            value = { text: "0" }
                            unit = { text: "tasks" }
                            trend = { text: "" }
                        }

                        actions_card = <MetricsCard> {
                            label = { text: "Total Actions" }
                            value = { text: "0" }
                            unit = { text: "actions" }
                            trend = { text: "" }
                        }
                    }
                }

                // Secondary metrics (3 columns compact)
                <View> {
                    width: Fill,
                    height: Fit,
                    flow: Right,
                    spacing: 12,

                    volume_card = <MetricsCardCompact> {
                        label = { text: "Execution Volume" }
                        value = { text: "0" }
                        unit = { text: "" }
                    }

                    failure_card = <MetricsCardCompact> {
                        label = { text: "Failure Rate" }
                        value = { text: "0.0" }
                        unit = { text: "%" }
                    }

                    recovery_card = <MetricsCardCompact> {
                        label = { text: "Recovery Rate" }
                        value = { text: "0.0" }
                        unit = { text: "%" }
                    }
                }
            }
        }
    }

    TraceTab = <View> {
        width: Fill,
        height: Fill,
        padding: 20,
        flow: Down,
        spacing: 10,

        <Label> {
            text: "Trace & Logs",
            draw_text: {
                color: #fff,
                text_style: { font_size: 18.0 }
            }
        }

        placeholder = <Label> {
            text: "Logs and FSM history will be displayed here (Phase 3)",
            draw_text: {
                color: #888,
                text_style: { font_size: 12.0 }
            }
        }
    }

    MemoryTab = <View> {
        width: Fill,
        height: Fill,
        padding: 20,
        flow: Down,
        spacing: 10,

        <Label> {
            text: "Memory Monitor",
            draw_text: {
                color: #fff,
                text_style: { font_size: 18.0 }
            }
        }

        placeholder = <Label> {
            text: "Memory monitoring will be displayed here (Phase 4)",
            draw_text: {
                color: #888,
                text_style: { font_size: 12.0 }
            }
        }
    }

    GameBenchmarkTab = <View> {
        width: Fill,
        height: Fill,
        padding: 20,
        flow: Down,
        spacing: 10,

        <Label> {
            text: "Game Benchmark",
            draw_text: {
                color: #fff,
                text_style: { font_size: 18.0 }
            }
        }

        game_state_display = <Label> {
            width: Fill,
            text: "Waiting for game data...",
            draw_text: {
                color: #ddd,
                text_style: { font_size: 12.0 },
                wrap: Word
            }
        }
    }

    // Main tab view component
    TabView = <View> {
        width: Fill,
        height: Fill,
        flow: Down,
        spacing: 0,

        show_bg: true,
        draw_bg: {
            color: #1e1e2e
        }

        // Tab bar
        <View> {
            width: Fill,
            height: Fit,
            flow: Right,
            spacing: 0,
            show_bg: true,
            draw_bg: { color: #2a2a38 }

            tab_bar = <View> {
                width: Fill,
                height: Fit,
                flow: Right,
                spacing: 2,

                agent_tab = <TabButtonActive> {
                    text: "Agent Benchmark"
                }

                trace_tab = <TabButton> {
                    text: "Trace & Logs"
                }

                memory_tab = <TabButton> {
                    text: "Memory"
                }

                game_tab = <TabButton> {
                    text: "Game State"
                }
            }
        }

        // Tab content area
        content = <View> {
            width: Fill,
            height: Fill,

            agent_content = <AgentBenchmarkTab> { visible: true }
            trace_content = <TraceTab> { visible: false }
            memory_content = <MemoryTab> { visible: false }
            game_content = <GameBenchmarkTab> { visible: false }
        }
    }
}
