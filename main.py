import base64
from enum import Enum
import os
import pprint
from datetime import datetime
from textwrap import dedent

import polars as pl
import structlog
from dotenv import load_dotenv
from openai import OpenAI
# from openinference.instrumentation.openai import OpenAIInstrumentor
# from phoenix.otel import register
from pydantic import BaseModel


def main():
    load_dotenv(override=True)
    log = structlog.get_logger()

    # tracer_provider = register(
    #     # endpoint="http://localhost:6006/v1/traces",
    #     # endpoint="http://host.docker.internal:6007/v1/traces",
    #     endpoint=os.getenv("PHOENIX_ENDPOINT"),
    # )
    # OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)

    image_paths = find_image_paths()
    log.info("Found images to process", count=len(image_paths), image_paths=image_paths)

    client = OpenAI()

    template_csv_name = "outputs/output.template.csv"
    current_time = datetime.now().strftime("%Y%m%d_%H%M")
    output_csv_name = f"outputs/output_{current_time}.csv"

    df = pl.read_csv(template_csv_name)

    for image_path in image_paths:
        log.info("Processing image", image_path=image_path)
        intake_forms: IntakeForms = process_image(client, image_path)
        log.info("Received result", result=pprint.pformat(intake_forms.model_dump()))
        for intake_form in intake_forms.list_of_intake_forms:
            df = df.vstack(
                pl.DataFrame(
                    {
                        "id_number": [intake_form.id_number],
                        "species": [intake_form.species],
                        "condition": [intake_form.condition],
                        "intake_date": [intake_form.intake_date],
                        # Making these wrap in one cell, separated by spaces and not newlines, to meet requirements
                        "rescuer_name": [
                            f"{intake_form.rescuer_name}                    {intake_form.rescuer_city}"
                        ],
                        "county_found": [intake_form.county_found],
                        "final_disposition": [intake_form.final_disposition],
                        "county_released": [intake_form.county_released],
                        "disposition_date": [intake_form.disposition_date],
                    }
                )
            )
        log.info("Appended to dataframe", df_length=len(df), image_path=image_path)

        # log.info("Appending to output CSV", output_csv_name=output_csv_name)
        # append_to_output_csv(intake_forms=result, output_csv_name=output_csv_name)

    log.info("Finished processing images")

    # # Sort by ID number
    # df = df.sort("id_number")
    # log.info(
    #     "Sorted dataframe by ID number",
    #     df_length=len(df),
    #     id_numbers=df["id_number"].to_list(),
    # )

    log.info(
        "Writing to output CSV", output_csv_name=output_csv_name, df_length=len(df)
    )
    df.write_csv(output_csv_name)
    # log.info("Sorting csv by ID number", output_csv_name=output_csv_name)
    # sort_csv_by_id(output_csv_name)


def find_image_paths():
    images_directory = "inputs/images"

    image_filenames = [
        filename
        for filename in os.listdir(images_directory)
        if filename.lower().endswith((".jpg"))
    ]

    image_paths = [
        os.path.join(images_directory, filename) for filename in image_filenames
    ]

    image_paths.sort(key=lambda p: os.path.getmtime(p))

    return image_paths


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


# def load_csv_into_string(csv_path):
#     with open(csv_path, "r") as csv_file:
#         return csv_file.read()


class IntakeForm(BaseModel):
    id_number: str
    species: str
    condition: str
    intake_date: str
    rescuer_name: str | None
    rescuer_city: str | None
    county_found: str | None
    final_disposition: str | None
    county_released: str | None
    disposition_date: str | None


class IntakeForms(BaseModel):
    list_of_intake_forms: list[IntakeForm]


def process_image(
    client: OpenAI,
    image_path: str,
) -> IntakeForms:
    model = os.getenv("OPENAI_MODEL")
    base64_image = encode_image(image_path)

    # previous_years_reports: str
    # for filename in os.listdir("inputs/previous_years_reports"):
    #     if filename.endswith(".csv"):
    #         with open(f"inputs/previous_years_reports/{filename}", "r") as f:
    #             previous_years_reports = f.read()

    # Get a list of conditions from the previous years' reports
    df = pl.read_csv("inputs/previous_years_reports/DNR-2020.csv")
    conditions = df["Condition"].unique().to_list()
    species = df["Species"].unique().to_list()

    system_prompt = dedent(
        f"""
        You are a helpful assistant who is helping a user to extract data from pictures of intake forms.

        Each intake form image could apply to just one ID or multiple IDs.
        In the case of multiple IDs, return an entry for each, with the same data if differences aren't specified.
        The ID numbers should range from 1 to 2000, alternately written as 24-0001 to 24-2000.
        For example, if you see an ID of 081-084, you should return a list of IntakeForm objects with IDs of 24-0081, 24-0082, 24-0083, and 24-0084.

        Additional notes:
          - CAGO is an abbreviation for Canada Goose
          - You MUST return dates in the format MM.DD.YY, like 11.30.24
          - You MUST abbreviate Indianapolis as Indpls
          - If the final_disposition is "D" or "E", then the county_released MUST be "N/A"
          - The final_disposition MUST be one of the following: D, R, or E
          - The counties MUST all be counties from the state of Indiana
          - The id_number MUST be between 24-0001 and 24-2000
          - On the form, county_found is written as "Co Found"
          - On the form, county_released is written as "Co Rel"
          - On the form, final_disposition is written as "Final Disp"
          - On the form, disposition_date is written as "DT"

        Refer to a list of conditions from previous years' report and follow the style of the condition notes:
        {conditions}

        The species SHOULD be one of the following:
        {species}

        Thank you so much for your help!
        """
    )

    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is in this image?",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            },
        ],
        response_format=IntakeForms,
    )

    return completion.choices[0].message.parsed


# def append_to_output_csv(intake_forms: IntakeForms, output_csv_name: str):
#     with open(output_csv_name, "a") as output_csv:
#         for intake_form in intake_forms.list_of_intake_forms:
#             # Putting these in one cell, separated by several spaces so it wraps in the cell, to meet requirements
#             rescuer_info = f'"{intake_form.rescuer_name}                {intake_form.rescuer_city}"'
#             output_csv.write(
#                 f"{intake_form.id_number},{intake_form.species},{intake_form.condition},{intake_form.intake_date},{rescuer_info},{intake_form.county_found},{intake_form.final_disposition},{intake_form.county_released},{intake_form.disposition_date}\n"
#             )


# def sort_csv_by_id(output_csv_name: str):
#     df = pl.read_csv(output_csv_name)
#     df = df.sort_values(by=["id_number"])
#     df.to_csv(output_csv_name, index=False)


if __name__ == "__main__":
    main()
