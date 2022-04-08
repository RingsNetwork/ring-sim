use async_process::Command;
use netsim_embed::Ipv4Range;
use netsim_embed::Netsim;
use netsim_embed::run;
use netsim_embed_machine::Namespace;

#[test]
fn test_nat() {
    run(async {
        let mut netsim = Netsim::<String, String>::new();
        let net1 = netsim.spawn_network(Ipv4Range::global());
        let mut server = Command::new("ncat");
        server.args(&["-l", "-4", "-p", "4242", "-c", "echo '<Hello World'"]);

        let server = netsim.spawn_machine(server, None).await;
        netsim.plug(server, net1, None).await;
        let server_addr = netsim.machine(server).addr();
        println!("Server Addr {}:4242", server_addr.to_string());

        let _ns = Namespace::current().expect("failed to get current namespace");
        let ns_server = netsim.machine(server).namespace();
        println!("{}", ns_server);
        netsim.machine(server).namespace().enter().expect("failed to enter");

        let mut cmd = Command::new("nc");
        cmd.args(&[&*server_addr.to_string(), "4242"]);
        let output = cmd.output().await.expect("failed on await output");
        println!("response: {}", std::str::from_utf8(&output.stdout).unwrap());

    })
}