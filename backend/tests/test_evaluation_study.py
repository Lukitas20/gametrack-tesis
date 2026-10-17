"""Sólo contratos y errores con archivos temporales; no son juicios humanos."""
import csv
import json
import pytest
from scripts.evaluation_study import capture, combine, metrics, usability
from scripts.check_test_environment import load, test_database_url as validate_database


def packet(tmp_path,n=3):
    path=tmp_path/"response.json"
    response={"schema":"gametrack-evaluation-capture-v1","kind":"recommendations","context":{},
              "response":{"items":[{"game":{"id":i},"score":.2} for i in range(n)]}}
    path.write_text(json.dumps(response),encoding="utf-8")
    output=tmp_path/"packet";capture(path,output)
    return output,path


def test_capture_preserves_bytes_and_leaves_all_judgments_missing(tmp_path):
    output,source=packet(tmp_path)
    assert (output/"response.json").read_bytes()==source.read_bytes()
    rows=list(csv.DictReader((output/"annotations.csv").open()))
    assert len(rows)==3 and all(row["label"]==row["elapsed_seconds"]=="" for row in rows)
    result=metrics(output,output/"annotations.csv",None)
    assert result["missing"]==3 and result["metrics"] is None
    with pytest.raises(FileExistsError): capture(source,output)


def test_modified_capture_or_duplicate_judgments_are_rejected(tmp_path):
    output,_=packet(tmp_path)
    annotation=output/"annotations.csv"
    with annotation.open("a") as stream: stream.write("0,relevance,1,isolated-test,,,\n")
    with pytest.raises(ValueError,match="duplicado"): metrics(output,annotation,None)
    (output/"response.json").write_text("{}")
    with pytest.raises(ValueError,match="modificado"): metrics(output,annotation,None)


def test_complete_labels_do_not_bypass_training_separation(tmp_path):
    output,_=packet(tmp_path,n=20)
    path=output/"annotations.csv"
    rows=list(csv.DictReader(path.open()))
    for row in rows: row.update(label="1",annotator="isolated-unit-test")
    with path.open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    assert metrics(output,path,None)["status"]=="split_not_verified"


@pytest.mark.parametrize("url",["postgresql://localhost/gametrack","sqlite:///prod.db"])
def test_restoration_refuses_operational_database_names(url):
    with pytest.raises(ValueError): validate_database(url)


def test_load_refuses_production_before_network():
    with pytest.raises(ValueError): load("https://gametrack-591398735968.us-central1.run.app",1,1)


def test_combining_keeps_originals_and_run_boundaries(tmp_path):
    output,source=packet(tmp_path)
    before=(output/"response.json").read_bytes()
    other=tmp_path/"other";other.mkdir();second,_=packet(other,n=4)
    combined=tmp_path/"combined";combine([output,second],combined)
    assert (output/"response.json").read_bytes()==before
    cases=json.loads((combined/"cases.json").read_text())
    assert len({case["id"] for case in cases})==7
    assert {case["run_id"] for case in cases}=={"0","1"}
    with pytest.raises(ValueError,match="dos veces"): combine([output,output],tmp_path/"duplicate")


def test_usability_instrument_has_no_invented_completion_or_time(tmp_path):
    tasks=tmp_path/"tasks.json";tasks.write_text(json.dumps([{"id":"T1","instruction":"Consigna aislada de prueba"}]))
    output=tmp_path/"use";usability(tasks,output)
    result=metrics(output,output/"annotations.csv",None)
    assert result["missing"]==1 and result["seconds"] is None and result["metrics"] is None
