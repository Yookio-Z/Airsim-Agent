from setuptools import setup

package_name = "airsim_agent_ros"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", ["config/providers.yaml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="AirSim Agent Team",
    maintainer_email="dev@example.com",
    description="ROS2 Provider Gateway for PX4 and third-party ROS algorithms.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "gateway_node = airsim_agent_ros.gateway_node:main",
        ],
    },
)
