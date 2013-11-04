##Congress

Copyright (c) 2013 VMware, Inc. All rights reserved.

####Compilation

  To compile the source execute the following command from the root directory

  ```bash
  make
  ```

####API server

#####Install Pre-requisites

  Make sure Python packages `python-openvswitch` and `python-ldap` are installed

  * On Unbuntu run the following command  to install `python-openvswitch` package
  
  ```bash
  sudo apt-get python-openvswitch
  ```
  * To install `python-ldap` python package execute the following command

  ```bash
  sudo easy_install python-ldap
  ```
#####Run the API Server

  * From the root directory execute the following command
  
  ```bash
  ./scripts/run_api_server
  ```
  This starts the API server. It listens on `http://0.0.0.0:8080` for incoming HTTP requests.

####Run the unit tests

   **Pre-requisites** : Python packages`nosetests` python package is installed.


   From the root directory run the following command
  
   ```bash
   ./scripts/run_tests
   ```

