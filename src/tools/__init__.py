"""工具层（原子工具注册组）。

文件地图——每个文件一个职责域；依赖方向只有一条：**工具文件 → FlightController/感知轴**，
没有任何工具文件反向 import agent 层（注册统一由 ``src/agent/tool_executor.py`` 做）：

    manifest.py          工具清单：名称/类别/能力/模型可见性的依据（零依赖，契约测试的锚）
    flight.py            飞行工具组：链路/遥测/飞行指令（drone_*，后端中立）
    mission.py           任务工具组：上传/下载/清空/启动/进度（GCS 专用，模型面没卡片）
    sensors.py           传感器工具组：AirSim 仿真传感器（airsim_get_sensors）
    perception_axis.py   感知轴工具组：perception_status / inspect_current_frame / locate_image_point
    providers.py         Provider 工具组：ROS2 桥（冻结期，整体可裁）

注册组按需懒加载：可选依赖（AirSim/PX4/感知轴）不应拖累无关后端。更高层的
"怎么做一类任务"属于 markdown 技能，不是工具。
"""

__all__ = [
    "register_flight_tools",
    "register_mission_tools",
    "register_sensor_tools",
    "register_perception_axis_tools",
    "register_provider_tools",
]


def __getattr__(name: str):
    """Load optional tool groups only when the active backend needs them."""
    if name == "register_flight_tools":
        from .flight import register_flight_tools

        return register_flight_tools
    if name == "register_mission_tools":
        from .mission import register_mission_tools

        return register_mission_tools
    if name == "register_sensor_tools":
        from .sensors import register_sensor_tools

        return register_sensor_tools
    if name == "register_perception_axis_tools":
        from .perception_axis import register_perception_axis_tools

        return register_perception_axis_tools
    if name == "register_provider_tools":
        from .providers import register_provider_tools

        return register_provider_tools
    raise AttributeError(name)
