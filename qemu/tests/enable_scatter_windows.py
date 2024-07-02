import time

from virttest import error_context, utils_misc, utils_net, utils_test
from virttest.utils_windows import virtio_win


@error_context.context_aware
def run(test, params, env):
    """
    Change certain netkvm driver parameter value and
    check the setting result.

    1) start vm
    2) check and install wireshark and winpcap
    3) enable netkvm driver TxLSO
    4) start File Transfer and use WinDump to log traffic and Wireshark
       for analysis, some packets should be over 1514 in length.
    5) disable TxLSO and set MTU to 1000
    6) start file transfer and log file transfer traffic again,
       no packet length should over 1014

    param test: the test object
    param params: the test params
    param env: test environment
    """

    def _is_process_finished(session, process_name):
        """
        Check whether the target process is finished running
        param session: a guest session to send command
        param process_name: the target process name

        return: True if process does not exists,
                False if still exists
        """
        check_proc_cmd = check_proc_temp % process_name
        status, output = session.cmd_status_output(check_proc_cmd)
        if status:
            return False
        return process_name not in output

    def _start_windump_session():
        """
        Start a WinDump session and log network traffic to a file
        """
        error_context.context("Start WinDump session", test.log.info)
        session_serial = vm.wait_for_serial_login(timeout=timeout)
        guest_ip = vm.get_address()
        try:
            run_windump_cmd = run_windump_temp % (host_ip, guest_ip)
            status, output = session_serial.cmd_status_output(
                run_windump_cmd, timeout=timeout
            )
            if status:
                test.error(
                    "Failed to start WinDump session, status=%s, output=%s"
                    % (status, output)
                )
            is_started = utils_misc.wait_for(
                lambda: not _is_process_finished(
                    session_serial, params.get("windump_name")
                ),
                20,
                5,
                1,
            )
            if not is_started:
                test.error("Timeout when waiting for WinDump to start")
        finally:
            session_serial.close()

    def _stop_windump_session():
        """
        Stop the running WinDump session
        """
        error_context.context("Stop WinDump", test.log.info)
        status, output = session.cmd_status_output(stop_windump_cmd, timeout=timeout)
        if status:
            test.error(
                "Failed to stop WinDump: status=%s, output=%s" % (status, output)
            )

    def _parse_log_file(packet_filter):
        """
        Parse the log file generated by the previous wireshark session.

        param packet_filter: the filter to apply when dump packets
        return: the output of the parse result
        """
        error_context.context("Parse captured packets using Wireshark", test.log.info)
        parse_log_cmd = parse_log_temp % packet_filter
        status, output = session.cmd_status_output(parse_log_cmd, timeout=timeout)
        if status != 0:
            test.error(
                "Failed to parse session log file, status=%s, output=%s"
                % (status, output)
            )
        return output

    def _get_traffic_log(packet_filter):
        """
        Use WinDump to capture the file transfer network traffic,
        and return the packet analysis output.

        :param packet_filter: The filter to apply when analyzing packets
        :return: The output of the parse result
        """
        _start_windump_session()
        error_context.context("Start file transfer", test.log.info)
        utils_test.run_file_transfer(test, params, env)
        time.sleep(30)
        _stop_windump_session()
        return _parse_log_file(packet_filter)

    def _set_driver_param(index):
        """
        set the netkvm driver param's value.

        param index: the index of the list of target params
        """
        param_name = param_names[index]
        param_value = param_values[index]
        utils_net.set_netkvm_param_value(vm, param_name, param_value)

    def _get_driver_version(session):
        """
        Get current installed virtio driver version
        return: a int value of version, e.g. 191
        """
        query_version_cmd = params["query_version_cmd"]
        output = session.cmd_output(query_version_cmd)
        version_str = output.strip().split("=")[1]
        version = version_str.split(".")[-1][0:3]
        return int(version)

    timeout = int(params.get("timeout", 360))
    driver_verifier = params["driver_verifier"]
    autoit_name = params.get("autoit_name")
    run_windump_temp = params.get("run_windump_temp")
    stop_windump_cmd = params.get("stop_windump_cmd")
    check_proc_temp = params.get("check_proc_temp")
    parse_log_temp = params.get("parse_log_temp")
    param_names = params.get("param_names").split()
    param_values = params.get("param_values").split()

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    host_ip = utils_net.get_host_ip_address(params)

    session = vm.wait_for_login(timeout=timeout)
    # make sure to enter desktop
    vm.send_key("meta_l-d")
    time.sleep(30)
    error_context.context(
        "Check if the driver is installed and " "verified", test.log.info
    )
    session = utils_test.qemu.windrv_check_running_verifier(
        session, vm, test, driver_verifier, timeout
    )

    if _get_driver_version(session) > 189:
        param_names.append("*JumboPacket")
    else:
        param_names.append("MTU")

    error_context.context("Install winpcap", test.log.info)
    install_winpcap_cmd = params.get("install_winpcap_cmd")
    install_winpcap_cmd = utils_misc.set_winutils_letter(session, install_winpcap_cmd)
    status, output = session.cmd_status_output(install_winpcap_cmd, timeout=timeout)
    if status:
        test.error("Failed to install pcap, status=%s, output=%s" % (status, output))

    test.log.info("Wait for pcap installation to complete")
    autoit_name = params.get("autoit_name")
    utils_misc.wait_for(
        lambda: _is_process_finished(session, autoit_name), timeout, 20, 3
    )

    for process_name in ["windump", "tshark"]:
        check_cmd = params["check_%s_installed_cmd" % process_name]
        install_cmd = utils_misc.set_winutils_letter(
            session, params["%s_install_cmd" % process_name]
        )

        is_installed = process_name in session.cmd_output(check_cmd)

        if not is_installed:
            error_context.context("Install %s" % process_name, test.log.info)
            status, output = session.cmd_status_output(install_cmd, timeout=timeout)
            if status != 0:
                test.error(
                    "Failed to install %s, status=%s, output=%s"
                    % (process_name, status, output)
                )

            error_context.context(
                "Wait for %s installation to complete" % process_name, test.log.info
            )
            utils_misc.wait_for(
                lambda: _is_process_finished(session, process_name),
                timeout,
                20,
                3,
            )
            check_result = session.cmd_output(check_cmd).lower()
            if process_name not in check_result:
                test.error("%s installation failed to verify." % process_name)
            test.log.info("%s installed successfully.", process_name)
        else:
            test.log.info("%s is already installed", process_name)

    session.close()

    virtio_win.prepare_netkvmco(vm)
    error_context.context("Enable scatter gather", test.log.info)
    _set_driver_param(0)

    session = vm.wait_for_login(timeout=timeout)
    error_context.context(
        "Log network traffic with scatter gather enabled", test.log.info
    )
    output = _get_traffic_log("frame.len>1514")
    test.log.info("Check length > 1514 packets")
    if "Len" not in output:
        test.fail("No packet length >= 1514, output=%s" % output)
    session.close()

    error_context.context("Disable scatter gather", test.log.info)
    _set_driver_param(1)
    _set_driver_param(2)

    session = vm.wait_for_login(timeout=timeout)
    error_context.context(
        "Log network traffic with scatter gather disabled", test.log.info
    )
    test.log.info("Check length > 1014 packets")
    output = _get_traffic_log("frame.len>1014")
    if "Len" in output:
        test.fail("Some packet length > 1014, output=%s" % output)
