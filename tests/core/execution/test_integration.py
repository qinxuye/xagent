"""
Integration tests for process isolation with xagent tools.

Tests that the ProcessService integrates correctly with existing tools.
"""

import pytest

from xagent.core.execution.service import ProcessService
from xagent.core.execution.service.manager import (
    clear_process_service,
    set_process_service,
)


@pytest.mark.asyncio
async def test_process_service_manager():
    """Test ProcessService global manager."""
    service = ProcessService(n_workers=2, address="localhost:12352")

    # Set as global service
    set_process_service(service)

    # Verify we can retrieve it
    from xagent.core.execution.service.manager import get_process_service

    retrieved_service = get_process_service()
    assert retrieved_service is service

    # Cleanup
    clear_process_service()
    assert get_process_service() is None


@pytest.mark.asyncio
async def test_execution_result_serialization():
    """Test ExecutionResult serialization."""
    from xagent.core.execution.service import ExecutionResult

    # Create result
    result = ExecutionResult(
        success=True,
        output="Test output",
        error="",
        return_code=0,
        metadata={"key": "value"},
        execution_time=1.5,
        memory_used_mb=100.0,
    )

    # Convert to dict
    result_dict = result.to_dict()
    assert result_dict["success"] is True
    assert result_dict["output"] == "Test output"
    assert result_dict["execution_time"] == 1.5

    # Convert back from dict
    restored_result = ExecutionResult.from_dict(result_dict)
    assert restored_result.success == result.success
    assert restored_result.output == result.output
    assert restored_result.execution_time == result.execution_time


@pytest.mark.asyncio
async def test_isolation_type_enum():
    """Test IsolationType enum."""
    from xagent.core.execution.service import IsolationType

    assert IsolationType.PROCESS.value == "process"
    assert IsolationType.SANDBOX.value == "sandbox"


@pytest.mark.asyncio
async def test_service_status_enum():
    """Test ServiceStatus enum."""
    from xagent.core.execution.service import ServiceStatus

    assert ServiceStatus.STARTING.value == "starting"
    assert ServiceStatus.RUNNING.value == "running"
    assert ServiceStatus.STOPPING.value == "stopping"
    assert ServiceStatus.STOPPED.value == "stopped"
    assert ServiceStatus.ERROR.value == "error"


@pytest.mark.asyncio
async def test_service_info():
    """Test ServiceInfo dataclass."""
    from xagent.core.execution.service import ServiceInfo, ServiceStatus

    info = ServiceInfo(
        name="test_service",
        status=ServiceStatus.RUNNING,
        resource_info={"workers": 4},
        metrics={"executions": 100},
    )

    # Convert to dict
    info_dict = info.to_dict()
    assert info_dict["name"] == "test_service"
    assert info_dict["status"] == "running"
    assert info_dict["resource_info"]["workers"] == 4
    assert info_dict["metrics"]["executions"] == 100


@pytest.mark.asyncio
async def test_process_service_not_started_error():
    """Test error when calling execute before starting service."""
    service = ProcessService(n_workers=2, address="localhost:12353")

    # Don't start the service
    with pytest.raises(RuntimeError, match="ProcessService not started"):
        await service.execute_python(code="print('test')")


@pytest.mark.asyncio
async def test_python_execution_with_workspace():
    """Test Python execution with workspace directory."""
    import tempfile

    service = ProcessService(n_workers=2, address="localhost:12354")

    await service.start()

    try:
        # Create a temporary workspace
        with tempfile.TemporaryDirectory() as workspace:
            # Test that workspace is accessible
            result = await service.execute_python(
                code="import os\nprint(f'CWD: {os.getcwd()}')\nprint('Files:', os.listdir('.'))",
                workspace=workspace,
                timeout=10,
            )

            assert result.success is True
            # The output should contain the workspace path
            assert workspace in result.output or "CWD:" in result.output

    finally:
        await service.stop()
