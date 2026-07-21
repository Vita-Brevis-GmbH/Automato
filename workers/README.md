# Worker Nodes

Ein Unterordner pro Zielplattform. Jeder Worker:
- ist unabhängig horizontal skalierbar
- besitzt nur Credentials für seine eigene Zielplattform (Least Privilege)
- kommuniziert ausschließlich mit dem Orchestrator (Job annehmen, Ergebnis melden)
- ruft zur Ausführung die Ansible-Rollen/Playbooks aus `/ansible` auf

| Ordner | Zielplattform |
|---|---|
| fortigate/ | Fortinet FortiGate Firewalls |
| cisco-aci/ | Cisco ACI |
| vmware/ | VMware |
| proxmox/ | Proxmox |
| windows/ | Windows |
| linux/ | Linux |
| cisco-switches/ | Cisco Switches |
