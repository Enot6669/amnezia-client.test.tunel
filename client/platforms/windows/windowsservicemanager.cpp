/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "windowsservicemanager.h"

#include <QApplication>
#include <QTimer>

#include "Windows.h"
#include "Winsvc.h"
#include "logger.h"
//#include "mozillavpn.h"
#include "platforms/windows/windowsutils.h"

namespace {
Logger logger("WindowsServiceManager");
}

WindowsServiceManager::WindowsServiceManager(SC_HANDLE serviceManager,
                                             SC_HANDLE service)
    : QObject(qApp), m_serviceManager(serviceManager), m_service(service) {
  m_timer.setSingleShot(false);
}

std::unique_ptr<WindowsServiceManager> WindowsServiceManager::open(
    const QString serviceName) {
  LPCWSTR service = (const wchar_t*)serviceName.utf16();

  DWORD err = NULL;
  auto scm_rights = SC_MANAGER_CONNECT | SC_MANAGER_ENUMERATE_SERVICE |
                    SC_MANAGER_QUERY_LOCK_STATUS | STANDARD_RIGHTS_READ;
  auto manager = OpenSCManager(NULL,  // local computer
                               NULL,  // servicesActive database
                               scm_rights);
  if (manager == nullptr) {
    err = GetLastError();
    logger.error() << "OpenSCManager failed code: " << err;
    return {};
  }
  logger.debug() << "OpenSCManager access given";

  logger.debug() << "Opening Service - " << serviceName;
  auto serviceHandle = OpenService(
      manager, service,
      SERVICE_QUERY_STATUS | SERVICE_START | SERVICE_STOP);
  if (serviceHandle == nullptr) {
    err = GetLastError();
    CloseServiceHandle(manager);
    logger.error() << "OpenService failed code: " << err;
    return {};
  }

  logger.debug() << "Service manager execute access granted";
  return std::make_unique<WindowsServiceManager>(manager, serviceHandle);
}

WindowsServiceManager::~WindowsServiceManager() {
  if (m_service != NULL) {
    CloseServiceHandle(m_service);
  }
  if (m_serviceManager != NULL) {
    CloseServiceHandle(m_serviceManager);
  }
}

bool WindowsServiceManager::startPolling(DWORD goal_state, int max_wait_sec) {
  int tries = 0;
  while (tries < max_wait_sec) {
    SERVICE_STATUS status;
    if (!QueryServiceStatus(m_service, &status)) {
      WindowsUtils::windowsLog("Failed to retrieve the service status");
      return false;
    }

    if (status.dwCurrentState == goal_state) {
      if (status.dwCurrentState == SERVICE_RUNNING) {
        emit serviceStarted();
      }
      if (status.dwCurrentState == SERVICE_STOPPED) {
        emit serviceStopped();
      }
      return true;
    }

    logger.debug() << "Polling Status" << m_state_target
                   << "wanted, has: " << status.dwCurrentState;
    Sleep(1000);
    ++tries;
  }
  return false;
}

SERVICE_STATUS_PROCESS WindowsServiceManager::getStatus() {
  SERVICE_STATUS_PROCESS serviceStatus{};
  DWORD bytesNeeded = 0;
  if (!QueryServiceStatusEx(m_service, SC_STATUS_PROCESS_INFO,
                            reinterpret_cast<LPBYTE>(&serviceStatus),
                            sizeof(serviceStatus), &bytesNeeded)) {
    WindowsUtils::windowsLog("QueryServiceStatusEx failed");
    // Treat an unknown state as pending so callers fail closed.
    serviceStatus.dwCurrentState = SERVICE_STOP_PENDING;
  }
  return serviceStatus;
}

bool WindowsServiceManager::startService() {
  auto state = getStatus().dwCurrentState;
  if (state == SERVICE_RUNNING) {
    emit serviceStarted();
    return true;
  }
  if (state == SERVICE_START_PENDING) {
    return startPolling(SERVICE_RUNNING, 30);
  }
  if (state == SERVICE_STOP_PENDING &&
      !startPolling(SERVICE_STOPPED, 30)) {
    logger.error() << "Service did not reach STOPPED before start";
    return false;
  }

  if (!StartService(m_service, 0, NULL)) {
    const DWORD error = GetLastError();
    if (error != ERROR_SERVICE_ALREADY_RUNNING) {
      logger.error() << "StartService failed:" << error;
      return false;
    }
  }
  logger.debug() << "Service start requested";
  return startPolling(SERVICE_RUNNING, 30);
}

bool WindowsServiceManager::stopService() {
  auto state = getStatus().dwCurrentState;
  if (state == SERVICE_STOPPED) {
    emit serviceStopped();
    return true;
  }
  if (state == SERVICE_STOP_PENDING) {
    return startPolling(SERVICE_STOPPED, 30);
  }
  if (state == SERVICE_START_PENDING &&
      !startPolling(SERVICE_RUNNING, 30)) {
    logger.error() << "Service did not reach RUNNING before stop";
    return false;
  }

  SERVICE_STATUS status{};
  if (!ControlService(m_service, SERVICE_CONTROL_STOP, &status)) {
    const DWORD error = GetLastError();
    if (error != ERROR_SERVICE_NOT_ACTIVE) {
      logger.error() << "ControlService stop failed:" << error;
      return false;
    }
  }
  logger.debug() << "Service stop requested";
  return startPolling(SERVICE_STOPPED, 30);
}
