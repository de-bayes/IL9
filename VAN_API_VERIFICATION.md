# NGP VAN `people/find` verification (source-grounded)

## Scope of this note

This note has been revised to rely on the documentation text provided in review feedback (Parsons NGPVAN module docs) rather than guessing between competing payload structures.

## What is confirmed from the provided docs text

### 1) Person-find minimum match combinations
The docs text explicitly states that person find requires one of these minimum combinations:

- `first_name`, `last_name`, `email`
- `first_name`, `last_name`, `phone`
- `first_name`, `last_name`, `zip5`, `date_of_birth`
- `first_name`, `last_name`, `street_number`, `street_name`, `zip5`
- `email_address`

### 2) Address-based matching requires ZIP5
For address matching, the minimum documented combination includes ZIP5, so
`first + last + street number + street name` is not sufficient by itself.

### 3) Auth guidance used for VAN integrations
For VAN API integrations in this workflow, Basic Auth should use:

- username: application/integration identifier
- password: API key

(Teams should confirm the exact credential labels shown in their VAN/partner setup.)

## Correct implementation guidance for `/v4/people/find`

Based on the documented minimum combinations above, the safest address-based request is to include:

- first name
- last name
- street number
- street name
- ZIP5

Example payload shape to test:

```json
{
  "firstName": "Christine",
  "lastName": "De Groote",
  "streetNumber": "5555",
  "streetName": "N Sheridan Rd",
  "zipOrPostalCode": "60640"
}
```

> Why this shape: it matches the documented requirement set for street-based find (including ZIP5).

## cURL example

```bash
curl -u "YOUR_APPLICATION_NAME:YOUR_API_KEY" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -X POST "https://api.ngpvan.com/v4/people/find" \
  -d '{
    "firstName":"Christine",
    "lastName":"De Groote",
    "streetNumber":"5555",
    "streetName":"N Sheridan Rd",
    "zipOrPostalCode":"60640"
  }'
```

## Validation checklist

- If you receive `400`, inspect response field errors and align field names with your endpoint schema.
- If you receive `401/403`, verify credential mapping and key scope for the target database mode.
- If you receive `200` with no match, verify input normalization (street abbreviations, spacing, and ZIP5).

## Environment limitation in this runtime

Direct fetches to external VAN documentation/API hosts from this container were blocked by proxy (`403`), so endpoint behavior cannot be live-validated from this environment.
